#!/usr/bin/env python
# docker run -it --rm --env-file env_file -v /Users/john/Dropbox/atd/atd-knack-banner:/app atddocker/atd-knack-banner ./update_employees.py
"""
Get employee data from the human resource system (Banner) and update records in Knack
apps.
"""
import json
import logging
import os
import secrets
import string
import sys
import csv

import knackpy
import requests
import wddx
import smbclient


def parse_name(full_name):
    name_parts = full_name.split(",")
    return {"first": name_parts[1].strip(), "last": name_parts[0].strip()}


def to_string(val):
    return str(val) if val else None


def to_email(val):
    return {"email": val.lower()}


FIELD_MAP = [
    {"banner": "pidm", "dts_portal": "", "hr": "field_99", "primary_key": True},
    {"banner": "temp_status", "dts_portal": "", "hr": "field_95", },
    {"banner": "job_title", "dts_portal": "", "hr": "field_230", },
    {"banner": "email", "dts_portal": "", "hr": "field_18", "handler": to_email},
    {"banner": "empclass_desc", "dts_portal": "", "hr": "field_251", },
    {"banner": "divn_name", "dts_portal": "", "hr": "field_250", },
    {"banner": "fullname", "dts_portal": "", "hr": "field_17", "handler": parse_name, },
    {"banner": "posn", "dts_portal": "", "hr": "field_248", },
]

PASSWORD_FIELD = {"hr": "field_19", "dts_portal": ""}
USER_STATUS_FIELD = {"hr": "field_20", "dts_portal": ""}
EMAIL_FIELD = {"hr": "field_18", "dts_portal": ""}
ACCOUNTS_OBJS = {"hr": "object_5"}


def drop_empty_positions(records_hr, key="pidm"):
    """
    Data from Banner contains vacant positions. so we remove them if the record has no
    employee ID number, aka pidm
    :returns: employee list from banner with vacant positions removed
    """
    return [r for r in records_hr if r.get(key)]


def get_employee_data():
    """
    Request hr data from banner
    :return: employee list from banner with vacant positions removed
    """
    BANNER_API_KEY = os.getenv("BANNER_API_KEY")
    BANNER_URL = os.getenv("BANNER_URL")
    params = {
        "method": "getTransitEmployees",
        "setApiDept": "24E",
        "setApiKey": BANNER_API_KEY,
    }
    #  get data in wddx format
    res = requests.get(BANNER_URL, params=params)
    #  use module to parse wddx tags
    #  and read data as list (which is actually a JSON string)
    json_raw = wddx.loads(res.text)
    #  remove weird leading slashes from data contents
    json_clean = json_raw[0].replace("//", "")
    records_hr_unfiltered = json.loads(json_clean)
    return drop_empty_positions(records_hr_unfiltered)


def get_emails_data():
    """
    Read csv from shared drive with employee emails
    :return: dictionary of emails
    """
    employee_emails = {}

    smbclient.ClientConfig(username=os.getenv("SHAREDDRIVE_USERNAME"), password=os.getenv("SHAREDDRIVE_PASSWORD"))

    emails_csv = os.getenv("SHAREDDRIVE_FILEPATH")
    with smbclient.open_file(emails_csv, mode='r') as emails:
        reader = csv.DictReader(emails)
        data = [row for row in reader]

    for row in data:
        # check if employeeID exists for record
        if row.get("employeeID"):
            # all contractors have ID 999, this will not collect their emails
            employee_emails[row.get("employeeID")] = {
                "email": row.get("EmailAddress"),
                "name": row.get("Name")
            }

    return employee_emails


def update_emails(records_hr, employee_emails):
    """
    Compare records from banner to list of emails from CTM
    if emails do not match,
    :param records_hr: list of records from hr
    :param employee_emails: dictionary of employee ids / emails
    :return: records_hr with updated emails
    """
    in_banner_no_email = 0
    for r_hr in records_hr:
        pk_hr = r_hr["pidm"]
        try:
            employee = employee_emails[str(pk_hr)]
            if r_hr["email"] != employee["email"]:
                r_hr["email"] = employee["email"]
        except KeyError:
            in_banner_no_email = in_banner_no_email + 1
    print(f'{in_banner_no_email} records in banner are not in ctm email list (based on user id)')
    return records_hr


def map_records(records_hr, field_map, knack_app_name):
    """
    Map banner data to knack field names
    :param records_hr: list, records from banner
    :param field_map: list of field name mapping objects (banner -> knack) and optional field handler functions
    :param knack_app_name: string, which fields to map to, hr or dts
    :return: list, records from banner with knack field_names
    """
    records_mapped = []
    for record in records_hr:
        record_mapped = {}
        for field in field_map:
            field_name_banner = field["banner"]
            field_name_knack = field[knack_app_name]
            handler = field.get("handler")
            val_raw = record[field_name_banner]
            val = val_raw if not handler else handler(val_raw)
            record_mapped[field_name_knack] = val
        records_mapped.append(record_mapped)
    return records_mapped


def get_primary_key_field(field_map, knack_app_name):
    """
    :param field_map: banner and knack field mappings
    :param knack_app_name: knack app name to lookup in field_map
    :return: primary key field name, string
    """
    pk_field = [f for f in field_map if f.get("primary_key")][0]
    return pk_field[knack_app_name]


def is_different(record_hr, record_knack):
    """
    compare records by comparing field values
    :param record_hr: record from banner
    :param record_knack: record from knack
    :return: True if any values do not match between records
    """
    for key, val in record_hr.items():
        val_knack = record_knack[key]
        # unpack dicts, because the knack name field contains a "formatted_value" key
        # which we want to ignore, because it's a field config prop that we don't want
        # need to stay in sync w/
        if isinstance(val, dict):
            for _key, _val in val.items():
                if val_knack[_key] != val[_key]:
                    return True
            continue
        if val_knack != val:
            return True
    return False


def build_payload(records_knack, records_hr, pk_field, status_field, password_field):
    """
    compare the hr records against knack records and return those records which
    are different are new
    :param records_knack: Records from knack (knackpy record format)
    :param records_hr: field mapped records from banner
    :param pk_field: field name for primary key in knack app
    :param status_field: field name for status field in knack app
    :param password_field: field for password in knack app
    :return:
    """
    payload = []
    print(f"Generating payload...")
    # for each banner record check knack records comparing pk to see if banner record exists in knack
    for r_hr in records_hr:
        exists_in_knack = False
        pk_hr = r_hr[pk_field]
        for r_knack in records_knack:
            pk_knack = r_knack[pk_field]
            if pk_hr == pk_knack:
                exists_in_knack = True
                r_hr["id"] = r_knack["id"]
                # Check if user is marked as inactive in knack
                # and update status_field to active since they are in banner
                if r_knack[status_field] == "inactive":
                    r_hr[status_field] = "active"
                # if any of the fields differ, add banner record to payload
                if is_different(r_hr, r_knack):
                    payload.append(r_hr)
                break
        # employee id number not in knack records
        if not exists_in_knack:
            # A password field is required when creating new users. so we generate one here.
            # The user is expected to sign in with Active Directory, they will not use this password.
            r_hr[password_field] = random_password()
            # Knack's default user status is inactive. so set new users' status to active
            r_hr[status_field] = "active"
            payload.append(r_hr)

    inactivate = 0
    # identify users which are no longer in Banner and therefore need to be deactivated
    for r_knack in records_knack:
        matched = False
        pk_knack = r_knack[pk_field]
        for r_hr in records_hr:
            pk_hr = r_hr[pk_field]
            if pk_hr == pk_knack:
                matched = True
                break
        if not matched and r_knack[status_field] != "inactive":
            record_id = r_knack["id"]
            inactivate = inactivate + 1
            payload.append({"id": record_id, status_field: "inactive"})

    print(f"{inactivate} records to mark inactive.")

    return payload


def random_password(numchars=32):
    """ generate a random password with at least 1 lowercase, uppercase, and special
    char """
    # i don't know what knack considers a special character, but it's something less
    # than string.punctation
    special_chars = "!#$%&"
    chars = special_chars + string.digits + string.ascii_letters
    while True:
        password = "".join(secrets.choice(chars) for i in range(numchars))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in special_chars for c in password)
        ):
            break
    return password


def set_passwords(records, password_field):
    """ A password field is required when creating new users. so we generate one here.
    The user is expected to sign in with Active Directory, they will not use this 
    password."""
    for r in records:
        r[password_field] = random_password()
    return


def remove_empty_emails(payload, email_field):
    """
    Knack won't allow records to be added without valid emails
    :param payload: list of records payload
    :param email_field: email field to check from knack app
    :return: list of payload records with valid emails
    """
    cleaned_payload = []
    for r in payload:
        try:
            if r[email_field]["email"] != "no email":
                cleaned_payload.append(r)
        except KeyError:
            # if an item in the payload doesn't have an email
            # that payload item is being set as inactive
            cleaned_payload.append(r)
    return cleaned_payload


def format_errors(error_list, record):
    """ generate an error report that will be mildly readable in an email """
    separator = "-" * 10
    msgs = "\n".join([e["message"] for e in error_list])
    record_props = "\n".join([str(v) for v in record.values()])
    return f"{separator}\nError(s):\n{msgs}\n\nData:\n{record_props}\n\n"


def main():
    KNACK_APP_NAME = os.getenv("KNACK_APP_NAME")
    KNACK_APP_ID = os.getenv("KNACK_APP_ID")
    KNACK_API_KEY = os.getenv("KNACK_API_KEY")

    records_hr_banner = get_employee_data()
    employee_emails = get_emails_data()
    records_hr_emails = update_emails(records_hr_banner, employee_emails)
    records_mapped = map_records(records_hr_emails, FIELD_MAP, KNACK_APP_NAME)

    # use knackpy to get records from knack hr object
    knack_obj = ACCOUNTS_OBJS[KNACK_APP_NAME]
    app = knackpy.App(app_id=KNACK_APP_ID, api_key=KNACK_API_KEY)
    records_knack = app.get(knack_obj)

    pk_field = get_primary_key_field(FIELD_MAP, KNACK_APP_NAME)
    status_field = USER_STATUS_FIELD[KNACK_APP_NAME]
    password_field = PASSWORD_FIELD[KNACK_APP_NAME]
    email_field = EMAIL_FIELD[KNACK_APP_NAME]
    payload = build_payload(records_knack, records_mapped, pk_field, status_field, password_field)
    cleaned_payload = remove_empty_emails(payload, email_field)

    print(f"{len(cleaned_payload)} total records to process in Knack.")

    errors = []
    for record in cleaned_payload:
        method = "update" if record.get("id") else "create"
        try:
            app.record(data=record, method=method, obj=knack_obj)
        except requests.HTTPError as e:
            if e.response.status_code == 400:
                errors_list = e.response.json()["errors"]
                errors.append(format_errors(errors_list, record))
                continue
            else:
                # if we get an error that is not 400, that error is raised, but we won't see previous errors
                raise e

    print(f"Update complete. {len(errors)} errors.")
    return errors


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout)
    errors = main()
    if errors:
        raise Exception("".join(errors))

