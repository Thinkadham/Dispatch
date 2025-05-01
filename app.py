# This is a Streamlit application for managing dispatch records.
import streamlit as st
import pandas as pd
from supabase import create_client, Client
import os
from datetime import datetime, date # Ensure date is imported
import traceback # For detailed error logging
from io import BytesIO # Import BytesIO for in-memory file handling


# --- Streamlit Page Configuration (MUST be the first Streamlit command) ---
st.set_page_config(page_title="Dispatch Register", layout="wide")

# --- Hide Streamlit Style Elements (Hamburger Menu, Footer, Header) ---
hide_menu_style = """
        <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: visible;}
        header {visibility: hidden;}
        </style>
        """
st.markdown(hide_menu_style, unsafe_allow_html=True)

st.image("images/header.png")
st.title("Dispatch Register of Hydraulic Division Uri")

# --- Supabase Connection ---
# It's recommended to use environment variables or Streamlit secrets for these
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "YOUR_SUPABASE_URL")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "YOUR_SUPABASE_KEY")


# --- Supabase Table Names ---
DISPATCH_TABLE = 'dispatch_records'
CONTACTS_TABLE = 'contacts'
SEQUENCE_TABLE = 'dispatch_sequence'
USERS_TABLE = 'users' # New table for users


# --- Supabase Connection ---
# Initialize Supabase client only if URL and Key are provided (and not the placeholders)
supabase: Client = None
if SUPABASE_URL != "YOUR_SUPABASE_URL" and SUPABASE_KEY != "YOUR_SUPABASE_KEY":
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        st.error(f"Error connecting to Supabase: {e}")
        supabase = None
else:
    st.warning("Supabase URL and Key not configured. Please add them to Streamlit secrets or replace the placeholders.")
    supabase = None

# --- Helper Functions ---
def fetch_users():
    """Fetches all users from the Supabase users table for simple plaintext authentication."""
    if not supabase:
        st.error("Supabase connection not available.")
        return {} # Return empty dictionary if supabase is not connected
    try:
        # Fetch only username and name, as 'password' column does not exist based on the error.
        # Note: This means the simple plaintext authentication will not verify passwords.
        response = supabase.table(USERS_TABLE).select("username, name").execute()
        if response.data:
            users = {}
            for user in response.data:
                # Basic check for required keys before accessing
                if 'username' in user and 'name' in user:
                     users[user['username']] = {
                         'name': user['name']
                         # Password is not fetched as the column does not exist
                     }
                else:
                     st.warning(f"Skipping user record with missing data: {user.get('username', 'N/A')}")
            return users
        else:
            st.warning("No users found in the database. Please add users to the 'users' table in Supabase.")
            return {} # Explicitly return empty dictionary if no data
    except Exception as e:
        st.error(f"Exception fetching users: {e}")
        st.error(traceback.format_exc())
        return {} # Explicitly return empty dictionary on error


def fetch_data(start_date=None, end_date=None):
    """Fetches records from the Supabase table, optionally filtered by date range."""
    if not supabase:
        # Removed logging
        return pd.DataFrame() # Return empty if supabase is not connected
    try:
        query = supabase.table(DISPATCH_TABLE).select("*")

        # Ensure dates are formatted correctly for Supabase query (YYYY-MM-DD string)
        if start_date:
            query = query.gte('Date', str(start_date))
        if end_date:
            query = query.lte('Date', str(end_date))

        # Order by 'No' column if it exists and makes sense for sorting, else by Date/ID
        # Assuming 'No' format HDU/Section/Start-End might not sort chronologically well.
        # Let's sort by Date descending, then maybe by id descending as a tie-breaker.
        query = query.order('Date', desc=True).order('id', desc=True)

        response = query.execute()

        if response.data:
            df = pd.DataFrame(response.data)
            # Ensure 'Date' is datetime.date type for display and consistency
            if 'Date' in df.columns:
                df['Date'] = pd.to_datetime(df['Date']).dt.date # Keep as date object

            # Ensure 'No' column exists even if empty initially
            if 'No' not in df.columns:
                 df['No'] = None

            # Reorder columns for better display - ensure all potential columns are listed
            cols_order = ['No', 'Date', 'Section', 'Address', 'Subject', 'CC', 'Remarks', 'id', 'created_at']
            # Filter out columns not present in the dataframe before reordering
            cols_present = [col for col in cols_order if col in df.columns]
            df = df[cols_present]
            return df
        else:
            # Return empty DataFrame with expected columns if no data
            return pd.DataFrame(columns=['No', 'Date', 'Section', 'Address', 'Subject', 'CC', 'Remarks', 'id', 'created_at'])
    except Exception as e:
        st.error(f"Error fetching data: {e}")
        st.error(traceback.format_exc()) # Log full traceback
        return pd.DataFrame() # Return empty DataFrame on error

def count_cc_recipients(cc_list):
    """Counts items in the CC list (from multiselect)."""
    return len(cc_list) if cc_list else 0

def insert_data(section, date_val, address, cc_list, subject, remarks):
    """Inserts a new record using an atomic sequence number from a DB function."""
    if not supabase:
        st.error("Supabase connection not available.")
        return False
    try:
        # 1. Get the next sequential number from the database function
        try:
            rpc_response = supabase.rpc('get_next_dispatch_no').execute()
            # More robust error checking for RPC calls
            if hasattr(rpc_response, 'error') and rpc_response.error:
                 st.error(f"Error calling DB function 'get_next_dispatch_no': {rpc_response.error.get('message', 'Unknown RPC error')}")
                 return False
            elif hasattr(rpc_response, 'data') and rpc_response.data is not None: # Check data exists and is not None
                 next_dispatch_number = rpc_response.data
            else:
                 st.error(f"Failed to get next dispatch number. RPC response invalid. Full response: {rpc_response}")
                 return False
        except Exception as rpc_error:
             st.error(f"Exception calling DB function 'get_next_dispatch_no': {rpc_error}")
             st.error(traceback.format_exc())
             return False

        # 2. Calculate CC count (number of people in the CC list)
        cc_count = count_cc_recipients(cc_list)

        # 3. Calculate the End Number for the range
        # StartNo is next_dispatch_number, EndNo = StartNo + cc_count
        end_no = next_dispatch_number + cc_count

        # 4. Generate the final 'No' string in the format HDU/Section/StartNo-EndNo
        # Ensure StartNo and EndNo are the same if cc_count is 0
        if cc_count == 0:
            generated_no = f"HDU/{section}/{next_dispatch_number}"
        else:
            generated_no = f"HDU/{section}/{next_dispatch_number}-{end_no}"


        # 5. Convert CC list to comma-separated string for storage
        cc_string = ", ".join(cc_list) if cc_list else None

        # 6. Perform a single insert with the generated 'No'
        data_to_insert = {
            "Section": section,
            "Date": str(date_val), # Ensure date is string for Supabase
            "Address": address,
            "CC": cc_string,
            "Subject": subject,
            "Remarks": remarks,
            "No": generated_no # Include the generated range number directly
        }
        insert_response = supabase.table(DISPATCH_TABLE).insert(data_to_insert).execute()

        # 7. Check insertion result (Supabase-py v1+ style)
        if insert_response.data and len(insert_response.data) > 0:
            # 8. Update the sequence table to set last_no to end_no
            # This ensures the next number will be end_no + 1
            try:
                # Use the *end_no* calculated earlier as the new last_no
                update_response = supabase.table(SEQUENCE_TABLE).update({"last_no": end_no}).eq('id', 1).execute()
                # Check for errors in update response
                if not (hasattr(update_response, 'data') and update_response.data):
                     st.warning(f"Record saved with Dispatch No: {generated_no}, BUT failed to update sequence table. Next number may be incorrect. Response: {update_response}")
            except Exception as seq_error:
                st.warning(f"Record saved with Dispatch No: {generated_no}, BUT failed during sequence update: {seq_error}")
                st.warning(traceback.format_exc())

            st.success(f"Record added successfully with Dispatch No: {generated_no}")
            return True
        else:
            error_message = "Unknown error."
            # Check if Supabase returned an error object (newer versions might)
            if hasattr(insert_response, 'error') and insert_response.error:
                 error_message = insert_response.error.get('message', str(insert_response.error)) # Get message or string representation
            elif hasattr(insert_response, 'status_code') and insert_response.status_code != 201: # Check HTTP status if no error object
                 error_message = f"Insertion failed with status code {insert_response.status_code}. Response: {insert_response.data}"

            st.error(f"Failed to add record: {error_message}")
            return False
    except Exception as e:
        st.error(f"Error inserting/updating data: {e}")
        st.error(traceback.format_exc())
        return False

# --- Address/Contact Management (using Database) ---
def fetch_contacts():
    """Fetches all contacts from the database."""
    if not supabase:
        return [], [] # Return empty if supabase is not connected
    try:
        response = supabase.table(CONTACTS_TABLE).select("id, name").order('name').execute() # Select only needed fields
        if response.data:
            # Extract just the names for the dropdown lists
            contact_names = [contact['name'] for contact in response.data]
            return response.data, contact_names # Return full data and just names
        else:
            return [], [] # Explicitly return empty lists if no data
    except Exception as e:
        st.error(f"Error fetching contacts: {e}") # Show error in main area if sidebar fails
        st.error(traceback.format_exc()) # Log full traceback
        return [], [] # Explicitly return empty lists on error

def add_contact(name):
    """Adds a new contact to the database."""
    if not name or not name.strip():
        st.warning("Please enter a contact name.") # Show warning in main area
        return False

    if not supabase:
        st.error("Supabase connection not available.")
        return False
    try:
        # Check if contact already exists (case-insensitive check might be better depending on requirements)
        # Using ilike for case-insensitive matching (PostgreSQL specific)
        # check_response = supabase.table(CONTACTS_TABLE).select("id").ilike('name', name.strip()).execute()
        # Using eq for case-sensitive:
        check_response = supabase.table(CONTACTS_TABLE).select("id").eq('name', name.strip()).execute()

        if check_response.data and len(check_response.data) > 0:
            st.warning(f"Contact '{name.strip()}' already exists.")
            return False

        # Insert new contact
        insert_response = supabase.table(CONTACTS_TABLE).insert({"name": name.strip()}).execute()
        if insert_response.data and len(insert_response.data) > 0:
            st.success(f"Added contact '{name.strip()}'")
            return True
        else:
            error_message = "Unknown error."
            # Check if Supabase returned an error object (newer versions might)
            if hasattr(insert_response, 'error') and insert_response.error:
                 error_message = insert_response.error.get('message', str(insert_response.error))
            elif hasattr(insert_response, 'status_code') and insert_response.status_code != 201:
                 error_message = f"Add contact failed with status code {insert_response.status_code}. Response: {insert_response.data}"
            st.error(f"Failed to add contact: {error_message}")
            return False
    except Exception as e:
        st.error(f"Error adding contact: {e}")
        st.error(traceback.format_exc())
        return False

def update_contact(contact_id, new_name):
    """Updates an existing contact in the database."""
    if not new_name or not new_name.strip():
        st.warning("Please enter a contact name.")
        return False

    if not supabase:
        st.error("Supabase connection not available.")
        return False

    try:
        # Check if new name already exists for a *different* contact ID
        check_response = supabase.table(CONTACTS_TABLE).select("id").eq('name', new_name.strip()).execute()
        if check_response.data:
            for contact in check_response.data:
                if contact['id'] != contact_id:
                    st.warning(f"Contact name '{new_name.strip()}' already exists.")
                    return False

        # Update contact
        update_response = supabase.table(CONTACTS_TABLE).update({"name": new_name.strip()}).eq('id', contact_id).execute()
        if update_response.data and len(update_response.data) > 0:
            st.success(f"Updated contact to '{new_name.strip()}'")
            return True
        else:
            error_message = "Unknown error."
            # Check if Supabase returned an error object (newer versions might)
            if hasattr(update_response, 'error') and update_response.error:
                 error_message = update_response.error.get('message', str(update_response.error))
                 # Check for potential unique constraint violation if name didn't change but triggered check
                 if "duplicate key value violates unique constraint" in error_message:
                      # This case should ideally be caught by the check above, but handle defensively
                      st.warning(f"Contact name '{new_name.strip()}' likely already exists (or no change made).")
                      return False # Treat as non-success
            elif hasattr(update_response, 'status_code') and update_response.status_code != 200: # Check for non-200 status on update
                 error_message = f"Update contact failed with status code {update_response.status_code}. Response: {update_response.data}"

            st.error(f"Failed to update contact: {error_message}")
            return False
    except Exception as e:
        st.error(f"Error updating contact: {e}")
        st.error(traceback.format_exc())
        return False

def delete_contact(contact_id_to_delete, contact_name):
    """Deletes a contact from the database after checking usage."""
    if not supabase:
        st.error("Supabase connection not available.")
        return False
    try:
        # Check if contact is used in 'Address' field of dispatch_records
        # Note: This assumes 'Address' stores the *name*, not an ID. Adjust if it stores ID.
        check_address = supabase.table(DISPATCH_TABLE).select("id", count='exact').eq('Address', contact_name).execute()

        # Check if contact is used in 'CC' field (as part of a comma-separated string)
        # Using 'like' can be slow on large tables without proper indexing.
        # Consider more robust checks if performance becomes an issue.
        check_cc = supabase.table(DISPATCH_TABLE).select("id", count='exact').like('CC', f"%{contact_name}%").execute()

        address_count = check_address.count if check_address.count is not None else 0
        cc_count = check_cc.count if check_cc.count is not None else 0

        if address_count > 0 or cc_count > 0:
            usage_message = []
            if address_count > 0:
                usage_message.append(f"'{contact_name}' is used as Address in {address_count} record(s)")
            if cc_count > 0:
                usage_message.append(f"'{contact_name}' is mentioned in CC in {cc_count} record(s)")
            st.warning(f"Cannot delete: {', '.join(usage_message)}.")
            return False

        # Delete contact if not used
        delete_response = supabase.table(CONTACTS_TABLE).delete().eq('id', contact_id_to_delete).execute()
        if delete_response.data and len(delete_response.data) > 0:
            st.success(f"Contact '{contact_name}' deleted")
            return True
        else:
            error_message = "Unknown error."
            # Check if Supabase returned an error object (newer versions might)
            if hasattr(delete_response, 'error') and delete_response.error:
                 error_message = delete_response.error.get('message', str(delete_response.error))
            elif hasattr(delete_response, 'status_code') and delete_response.status_code != 200: # Check for non-200 status on delete
                 error_message = f"Delete contact failed with status code {delete_response.status_code}. Response: {delete_response.data}"

            st.error(f"Failed to delete contact: {error_message}")
            return False
    except Exception as e:
        st.error(f"Error deleting contact: {e}")
        st.error(traceback.format_exc())
        return False

# --- Fetch Users and Contacts ---
users = {}
contacts_data, contact_names = [], []
if supabase: # Only fetch if supabase client is initialized
    users = fetch_users()
    contacts_data, contact_names = fetch_contacts()

# --- Simple Login Logic ---
# Initialize session state for authentication status if not already present
if 'authentication_status' not in st.session_state:
    st.session_state['authentication_status'] = None
if 'username' not in st.session_state:
    st.session_state['username'] = None
if 'name' not in st.session_state:
    st.session_state['name'] = None

# Display login form if not authenticated
if st.session_state['authentication_status'] is None or st.session_state['authentication_status'] is False:
    st.subheader("Login")
    with st.form("login_form"):
        input_username = st.text_input("Username")
        input_password = st.text_input("Password", type="password")
        login_button = st.form_submit_button("Login")

        if login_button:
            # Note: Password check removed as the 'password' column does not exist in the database.
            # This login will currently only check if the username exists.
            if input_username in users:
                st.session_state['authentication_status'] = True
                st.session_state['username'] = input_username
                st.session_state['name'] = users[input_username]['name']
                st.success("Logged in successfully!")
                st.rerun() # Rerun to show authenticated content
            else:
                st.error("Incorrect username") # Modified error message
                st.session_state['authentication_status'] = False # Explicitly set to False on failure

# --- Main Application Logic ---
# Only show content if authenticated
if st.session_state['authentication_status']:
    # User is logged in
    st.sidebar.title(f"Welcome {st.session_state['name']}")

    # Logout button
    if st.sidebar.button('Logout'):
        st.session_state['authentication_status'] = False
        st.session_state['username'] = None
        st.session_state['name'] = None
        st.rerun() # Rerun to show login form

    st.sidebar.title("Navigation")
    menu = ["Record New Dispatch", "View Records", "Manage Contacts"]
    menu_icons = ["✍️", "📊", "👥"]
    menu_options = [f"{icon} {item}" for icon, item in zip(menu_icons, menu)]

    # Use index=0 to default to the first option if needed, or keep as is
    choice_with_icon = st.sidebar.radio("Menu", menu_options, label_visibility="collapsed")
    # Extract the actual choice text without the icon
    choice = choice_with_icon.split(" ", 1)[1]

    # --- About (Expandable Section) ---
    with st.sidebar.expander("About this App", expanded=False):
        st.write("""
            This Application is designed to manage dispatch records for the Hydraulic Division Uri. It allows users to:
            - Record new dispatches with details like section, date, address, CC recipients, subject, and remarks.
            - View existing dispatch records with filtering options.
            - Manage contacts for dispatches, including adding, editing, and deleting contacts.
            - Download records in Excel format.
            - The application uses Supabase as the backend database for storing dispatch records and contacts.
            - The dispatch number is generated automatically based on a sequence in the database, ensuring unique and sequential numbering.

                The Database structure includes:
            1.  **`dispatch_records` table:** This table stores the dispatch records. Ensure it has columns like `No`, `Date`, `Section`, `Address`, `CC`, `Subject`, `Remarks`, `id`, and `created_at`.
            2.  **`contacts` table:** This table stores contact information. Ensure it has columns like `id` and `name`.
            3.  **`dispatch_sequence` table:** This table is used to generate sequential dispatch numbers. It should have at least an `id` column (with a single row, e.g., `id = 1`) and a `last_no` column (integer) to store the last generated number.
            4.  **`get_next_dispatch_no` RPC function:** This database function is called to atomically get the next available dispatch number and increment the sequence. You will need to create this function in your Supabase SQL editor. A basic example (for PostgreSQL) might look like this:

               Developed by: `Mohammad Adham Wani` *(Th!nkSolutions)*
        """)

    if choice == "Record New Dispatch":
        st.subheader("✍️ Record a New Dispatch")
        st.divider()
        # Use columns for better layout
        with st.form("dispatch_form", clear_on_submit=True):
            col1, col2 = st.columns(2)

            with col1:
                # Section Dropdown
                sections = ["ACCTS", "ESTAB", "DB", "CAMP"] # Predefined sections
                dispatch_section = st.selectbox("Section*", options=sections, index=None, placeholder="Select section...", key="dispatch_section")
                # Address Dropdown (Uses contacts fetched earlier)
                dispatch_address = st.selectbox("Address*", options=contact_names, index=None, placeholder="Select address...", key="dispatch_address")

            with col2:
                # Date Input (Defaults to today, user can change)
                dispatch_date = st.date_input("Date*", value=date.today(), key="dispatch_date")
                # CC Multiselect (Uses contacts fetched earlier)
                dispatch_cc = st.multiselect("CC", options=contact_names, key="dispatch_cc", help="Select recipients for Carbon Copy")

            # Subject and Remarks below the columns
            dispatch_subject = st.text_area("Subject*", placeholder="Enter subject...", key="dispatch_subject")
            dispatch_remarks = st.text_area("Remarks", placeholder="Enter remarks (optional)...", key="dispatch_remarks")
            st.divider()

            # Submit Button
            submitted = st.form_submit_button("Add Record")
            if submitted:
                # Validation: Check required fields
                if not dispatch_section or not dispatch_date or not dispatch_address or not dispatch_subject:
                    st.warning("Please fill in all required fields marked with * (Section, Date, Address, Subject).")
                else:
                    # Call insert_data with selected values (dispatch_cc is already a list)
                    if insert_data(dispatch_section, dispatch_date, dispatch_address, dispatch_cc, dispatch_subject, dispatch_remarks):
                        # Form clears automatically on success due to clear_on_submit=True
                        # Optionally rerun to update contact list if needed, but likely not necessary here
                        pass
                    else:
                        # Error message is shown within insert_data
                        pass

    elif choice == "View Records":
        st.subheader("📊 View Dispatch Records")
        st.divider()

        # Date range filter
        col_start_date, col_end_date = st.columns(2)
        with col_start_date:
            start_date_filter = st.date_input("Start Date", value=None, key="start_date_filter")
        with col_end_date:
            end_date_filter = st.date_input("End Date", value=None, key="end_date_filter")

        # Fetch data based on selected date range
        st.info("Fetching records...") # Keep this info message
        df_records = fetch_data(start_date=start_date_filter, end_date=end_date_filter)

        if not df_records.empty:
            st.write(f"Displaying {len(df_records)} records for the selected range.")
            # Display filtered data
            st.dataframe(df_records, use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("Download Options")

            col_excel, col_pdf = st.columns(2)

            with col_excel:
                # --- Excel Download ---
                try:
                    output_excel = BytesIO()
                    # Make a copy to avoid modifying the displayed df
                    df_excel = df_records.copy()
                    # Convert date objects to strings for Excel if needed
                    if 'Date' in df_excel.columns:
                        df_excel['Date'] = pd.to_datetime(df_excel['Date']).dt.strftime('%Y-%m-%d')

                    with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
                        df_excel.to_excel(writer, index=False, sheet_name='Dispatches')
                    excel_data = output_excel.getvalue()

                    # Use selected dates in the file name
                    excel_file_name = f'dispatch_records_{start_date_filter or "all"}_to_{end_date_filter or "all"}.xlsx'

                    st.download_button(
                        label="📄 Download as Excel (.xlsx)",
                        data=excel_data,
                        file_name=excel_file_name,
                        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        key='excel_download_btn'
                    )
                except ImportError:
                    st.error("Please install 'openpyxl' to enable Excel downloads. Run: pip install openpyxl")
                except Exception as e:
                    st.error(f"Error generating Excel file: {e}")
                    st.error(traceback.format_exc())

            with col_pdf:
                st.write("PDF generation can be added.")

        else:
            st.info("No records found for the selected date range.")

    elif choice == "Manage Contacts":
        st.subheader("👥 Manage Contacts")
        st.divider()

        # Initialize session state for editing if not already present
        if 'edit_contact_id' not in st.session_state:
            st.session_state.edit_contact_id = None
        if 'delete_contact_id' not in st.session_state:
            st.session_state.delete_contact_id = None
        if 'confirm_delete' not in st.session_state:
            st.session_state.confirm_delete = False
        if 'contact_to_delete_name' not in st.session_state:
            st.session_state.contact_to_delete_name = None


        # Display existing contacts with Edit/Delete options
        st.subheader("Existing Contacts")
        if contacts_data:
            # Use columns for layout: Name | Edit | Delete
            # Adjust column widths as needed
            cols = st.columns([0.6, 0.2, 0.2])
            cols[0].write("Name")
            cols[1].write("Edit")
            cols[2].write("Delete")
            st.divider() # Separator for header

            for contact in contacts_data:
                col1, col2, col3 = st.columns([0.6, 0.2, 0.2])
                col1.write(contact['name'])

                # Edit button
                if col2.button("Edit", key=f"edit_{contact['id']}"):
                    st.session_state.edit_contact_id = contact['id']
                    st.session_state.edit_contact_name = contact['name'] # Store current name for pre-filling form
                    st.session_state.confirm_delete = False # Reset delete confirmation
                    st.rerun() # Rerun to show edit form

                # Delete button
                if col3.button("Delete", key=f"delete_{contact['id']}"):
                    st.session_state.delete_contact_id = contact['id']
                    st.session_state.contact_to_delete_name = contact['name']
                    st.session_state.confirm_delete = True # Show confirmation
                    st.session_state.edit_contact_id = None # Reset edit state
                    st.rerun() # Rerun to show confirmation

        else:
            st.info("No contacts found.")

        st.divider()

        # --- Add New Contact Form ---
        st.subheader("Add New Contact")
        with st.form("add_contact_form", clear_on_submit=True):
            new_contact_name = st.text_input("Contact Name*", key="new_contact_name_input") # Changed key to avoid conflict
            add_contact_submitted = st.form_submit_button("Add Contact")

            if add_contact_submitted:
                if add_contact(new_contact_name):
                    # Refresh contacts list after adding
                    st.session_state.edit_contact_id = None # Reset edit state
                    st.session_state.delete_contact_id = None # Reset delete state
                    st.session_state.confirm_delete = False # Reset delete confirmation
                    st.rerun() # Rerun to update the displayed list


        st.divider()

        # --- Edit Contact Form (appears when a contact is selected for editing) ---
        if st.session_state.edit_contact_id is not None:
            st.subheader("Edit Contact")
            # Find the contact data for the selected ID
            contact_to_edit = next((item for item in contacts_data if item['id'] == st.session_state.edit_contact_id), None)

            if contact_to_edit:
                with st.form("edit_contact_form", clear_on_submit=False): # Don't clear on submit immediately
                    # Pre-fill the input with the current contact name
                    edited_contact_name = st.text_input("Edit Name*", value=st.session_state.edit_contact_name, key="edit_contact_name_input")
                    col_update, col_cancel = st.columns(2)
                    with col_update:
                        update_contact_submitted = st.form_submit_button("Update Contact")
                    with col_cancel:
                         cancel_edit = st.form_submit_button("Cancel")


                    if update_contact_submitted:
                        if update_contact(st.session_state.edit_contact_id, edited_contact_name):
                                # Clear edit state and rerun on successful update
                            st.session_state.edit_contact_id = None
                            st.session_state.edit_contact_name = None
                            st.rerun()
                    elif cancel_edit:
                            # Clear edit state and rerun on cancel
                            st.session_state.edit_contact_id = None
                            st.session_state.edit_contact_name = None
                            st.rerun()
            else:
                    st.warning("Contact not found for editing.")
                    st.session_state.edit_contact_id = None # Clear invalid edit state
                    st.session_state.edit_contact_name = None
                    st.rerun() # Rerun to clear the form area


            # --- Delete Contact Confirmation (appears when delete is clicked) ---
            if st.session_state.confirm_delete:
                st.subheader("Confirm Deletion")
                st.warning(f"Are you sure you want to delete contact '{st.session_state.contact_to_delete_name}'?")
                col_confirm_delete, col_cancel_delete = st.columns(2)
                with col_confirm_delete:
                    confirm_delete_button = st.button("Yes, Delete", key="confirm_delete_button")
                with col_cancel_delete:
                    cancel_delete_button = st.button("Cancel", key="cancel_delete_button")

                if confirm_delete_button:
                    if delete_contact(st.session_state.delete_contact_id, st.session_state.contact_to_delete_name):
                        # Clear delete state and rerun on successful deletion
                        st.session_state.delete_contact_id = None
                        st.session_state.contact_to_delete_name = None
                        st.session_state.confirm_delete = False
                        st.rerun()
                elif cancel_delete_button:
                    # Clear delete state and rerun on cancel
                    st.session_state.delete_contact_id = None
                    st.session_state.contact_to_delete_name = None
                    st.session_state.confirm_delete = False
                    st.rerun()
