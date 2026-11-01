# Timora

**Smart Employee Time Tracking**

Timora is a lightweight attendance and shift management system. Employees clock in and out on a simple attendance screen, and administrators manage employees, shifts, attendance records and reports from a secure admin panel.

It runs on your own computer or local network. It needs no cloud service, and all data stays in a local SQLite database.

## Contents

- [Features](#features)
- [Quick start on Windows](#quick-start-on-windows)
- [Default login](#default-login)
- [First-time setup](#first-time-setup)
- [Daily use](#daily-use)
- [Manual install (Windows, macOS, Linux)](#manual-install-windows-macos-linux)
- [Use from other computers on the network](#use-from-other-computers-on-the-network)
- [Deleting a shift](#deleting-a-shift)
- [Starter data](#starter-data)
- [How attendance rules work](#how-attendance-rules-work)
- [Overtime and pay](#overtime-and-pay)
- [Salary](#salary)
- [Roles and permissions](#roles-and-permissions)
- [Reports and Excel export](#reports-and-excel-export)
- [Backup and restore](#backup-and-restore)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure)
- [For developers](#for-developers)
- [Security notes](#security-notes)

## Features

- **Attendance screen:** large live clock, searchable employee dropdown, and one clear action: Clock In or Clock Out
- **Reliable clocking:** server time only, no duplicate clock-ins or clock-outs, and safe retries after a network failure
- **Shifts:** multiple shifts, overnight and flexible shifts, grace periods, working days, holidays and leave
- **Shift assignments:** individual or bulk, with effective dates and full history
- **Attendance tracking:** late arrival, early departure, overtime, half day and missing clock-outs
- **Corrections with a permanent trail:** every change records who, when, the old value, the new value and the reason
- **Employee types:** Technician, Manager and Cleaner by default, and you can change the list
- **Pay details and overtime:** basic salary and OT rate per employee, with overtime hours added by managers
- **Salary:** monthly salary with allowances, deductions and overtime, and a printable A5 salary slip
- **Reports:** eleven report types with filters, sorting and pagination, exportable as Excel, CSV or JSON, or printed to PDF
- **Export All Reports:** every report for the chosen dates and people, in one Excel workbook
- **Access control:** Super Admin, Admin, Attendance Manager and Viewer roles, enforced by the server
- **Backup and restore:** database backups, data exports and an automatic safety copy before every restore

Supports up to 999 employees, with IDs from `EM001` to `EM999`. IDs are permanent and never reused.

## Quick start on Windows

### 1. Get the code

**With Git:**

```bash
git clone https://github.com/JayashankaAnushan19/timora_attendence_shift_clockIOut_mng_system.git
cd timora_attendence_shift_clockIOut_mng_system
```

Or with SSH:

```bash
git clone git@github.com:JayashankaAnushan19/timora_attendence_shift_clockIOut_mng_system.git
cd timora_attendence_shift_clockIOut_mng_system
```

**Without Git:** on the GitHub page, choose **Code > Download ZIP**, then right-click the downloaded file and choose **Extract All**.

### 2. Start Timora

Double-click **`start-timora.bat`**.

That is the only step. The first start can take a few minutes, and later starts take a few seconds. The launcher will:

1. Look for Python 3.10 or newer. If it is missing, it installs Python 3.12 using `winget`, or from python.org if `winget` is unavailable.
2. Create a private environment in a `.venv` folder.
3. Install the required components. This needs internet the first time.
4. Create the database on first run.
5. Start Timora and open it in your browser.

If something goes wrong, the window explains the problem and how to fix it.

**Keep the window open while using Timora.** Closing it stops Timora.

| Page | Address |
|---|---|
| Attendance screen | http://127.0.0.1:5000/ |
| Admin panel | http://127.0.0.1:5000/admin |

## Default login

| Username | Password |
|---|---|
| `admin` | `admin@123` |

You must choose a new password the first time you log in. This default is public, so never skip this step.

**Forgot the password?** Open a terminal in the Timora folder and run:

```bash
.venv\Scripts\python.exe manage.py reset-password admin
```

## First-time setup

Log in to the admin panel, then:

1. **Set your timezone.** In **System Settings**, set **Business Timezone**. It starts as UTC, and all clock times and shift rules follow it, so this comes first.
2. **Enter your company details.** Set the company name and logo, then fill in **Company Profile**. These appear on salary slips and on the company card, which opens when anyone clicks the company name on the attendance screen. Everything on the card is visible without logging in.
3. **Review the shifts.** Under **Shifts**, edit these or create your own:

   | Shift | Starts | Ends | Days |
   |---|---|---|---|
   | Default Shift | Day 1, 08:00 AM | Day 2, 08:00 AM | Every day |
   | Midnight Shift | Day 1, 12:00 AM | Day 2, 12:00 AM | Every day |
   | Manager Shift | Day 1, 08:00 AM | Day 1, 05:00 PM | Monday to Friday |

   On the Default and Midnight shifts, employees can clock in at any time. Each attendance day starts at 8 AM on the Default Shift and at midnight on the Midnight Shift.

   When creating a shift, choose **Day 1** as the end day if it finishes the same day, or **Day 2** if it finishes the next day. Timora tells you if the times do not match the day you chose. A shift can last up to 24 hours.
4. **Add holidays.** On the **Shifts** page, open the **Holidays & Non-Working Days** tab.
5. **Add employees.** Under **Employees**, add each person with their type, shift, basic salary and OT pay rate per hour. IDs are generated automatically. To change the types or show a currency, use **Employee Settings** in **System Settings**.
6. **Add other administrators, if needed.** Use **User Access Management**. Anyone who adds overtime needs their own login with the **Manager** role or higher.

## Daily use

**Employees** use the attendance screen, with no login:

1. Open the **Select employee** dropdown, then find your name by typing your name or employee ID.
2. Check your name and status.
3. Press the green **CLOCK IN** or red **CLOCK OUT** button.

| Status | Meaning |
|---|---|
| NOT CLOCKED IN | You can clock in now |
| CLOCKED IN | You are clocked in and can clock out |
| CLOCKED OUT | Attendance for this shift is complete |
| SHIFT NOT STARTED | Clock-in has not opened yet; the screen shows when it opens |
| SHIFT COMPLETED | The shift has ended |
| NOT SCHEDULED | No shift today, or today is a day off, a holiday or leave |

**Administrators** use the admin panel to monitor the dashboard, correct records, record leave and run reports. Use **Admin login** at the bottom of the attendance screen, or go to `/admin`.

## Manual install (Windows, macOS, Linux)

Use these steps if you prefer the command line, or if you are not on Windows, where `start-timora.bat` does not apply. You need [Python 3.10 or newer](https://www.python.org/downloads/).

**Windows:**

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py --open
```

**macOS / Linux:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py --open
```

Press `Ctrl+C` to stop the server. To start it again later, run only the last command.

`run.py` accepts these options:

| Option | Purpose |
|---|---|
| `--open` | Open the browser when the server is ready |
| `--host 0.0.0.0` | Allow other computers on the network to connect |
| `--port 5050` | Use a different port |

## Use from other computers on the network

By default, only the computer running Timora can reach it. To let other computers connect:

1. Open `config/settings.json` in a text editor and change `host`:

   ```json
   {
     "host": "0.0.0.0",
     "port": 5000,
     "database": "database/timora.db",
     "backup_dir": "backups",
     "max_upload_mb": 200
   }
   ```

   To restore the default, set `host` back to `127.0.0.1`.

2. Restart Timora.
3. Allow Timora through Windows Firewall when asked, on **Private networks** only.
4. Find the server's address by running `ipconfig` and noting the **IPv4 Address**.
5. On the other computers, open `http://<that address>:5000/`, for example `http://192.168.1.20:5000/`.

Traffic is not encrypted on plain HTTP, so use this only on a network you trust. Read the [security notes](#security-notes) first.

## Deleting a shift

Super Admins and Admins can delete a shift from the **Shifts** page. Other roles cannot, even if they can manage shifts.

- Move everyone off the shift first under **Shift Assignments**. A shift with employees assigned today or later cannot be deleted.
- At least one shift must remain.
- Past attendance records keep the deleted shift's name, and its code can be used again.

To stop using a shift for now without removing it, choose **Deactivate** instead.

## Starter data

A new installation starts with the three starter shifts and one employee, **Heyshan Senaviratne** (`EM001`, Manager, on the Manager Shift). He comes from `config/starter_data.json`. His joining date is set to the installation date, so update it under **Employees**, along with his basic salary and OT rate.

This data is added only once, to an empty system. To start again from nothing, stop Timora and delete `database/timora.db`. This erases **all** data, including settings and admin accounts.

## How attendance rules work

All times are stored in UTC and shown in the business timezone. An overnight shift belongs to the date it starts on.

The examples use a 09:00 AM - 06:00 PM shift with a 10-minute grace period:

| Rule | How it works | Example |
|---|---|---|
| On time | Clock-in up to the end of the grace period | 09:05 AM is on time |
| Late | Clock-in after the grace period, measured from the shift start | 09:15 AM is 15 minutes late |
| Early arrival | Clock-in before the shift start, within the early allowance | 08:45 AM is 15 minutes early |
| Early departure | Clock-out before the shift end | 04:00 PM is 2 hours early |
| Overtime | Clock-out after the shift end, once past the minimum overtime (default 15 minutes) | 07:30 PM is 1h 30m overtime |
| Half day | Worked less than the half-day threshold (default 50%) of scheduled hours | |
| Incomplete attendance | No clock-out before the late clock-out allowance ends (default 4 hours) | An administrator can add the clock-out |

- **Clock-in window:** opens before the shift by the early allowance (default 60 minutes) and closes at the shift end.
- **Breaks:** the shift's break is deducted from worked time when the employee worked at least half of the scheduled shift.
- **Flexible shifts:** never flagged late or early, and overtime is based on hours worked.
- **24-hour shifts:** a shift that ends on Day 2 at its start time is a full 24-hour window. Marked flexible, as the Default and Midnight shifts are, it shows Present once the employee clocks out, with no late, early, half-day or overtime flags. On the Default Shift, a clock-in at 03:00 AM counts towards the attendance day that started at 8 AM the day before.
- **One status per record:** when several apply, the priority is Half Day, Late, Early Departure, Overtime, then Present. The durations are kept regardless, so reports count them all.
- **Absent:** worked out from the schedule when a scheduled shift ends with no attendance.
- **Past records:** each record keeps the shift times it was worked under, so editing a shift later never changes history.

Most of these values can be changed per shift or in **System Settings**.

## Overtime and pay

**Add overtime:** on the **Overtime** page, choose **Add Overtime**, enter the date, hours and reason, and choose selected employees or all active employees. To undo an entry, cancel it with a reason. Entries are never deleted.

**Who can do what:** the permissions are tied to the role on a person's login, not to their employee type. Marking someone as a Manager employee does not give them a login.

**How pay is calculated:**

- OT pay is added hours multiplied by the employee's OT rate per hour.
- Each entry keeps the rate in force when it was added, so changing a rate later does not change earlier pay. Set the rate before adding overtime.
- **Only hours added on the Overtime page count towards pay.** Overtime worked out from clock-out times appears in the **Overtime Report** but is not paid automatically, so nothing is paid twice or without a manager's approval.
- Added overtime is separate from attendance. It does not change clock times, worked hours or status, and it appears as its own line on the salary slip.

## Salary

Open **Salary** in the admin panel, available to Super Admins and Admins, and choose a month to see everyone's pay. Choose **View** on an employee to:

- Set their **basic salary** and **OT pay rate**.
- Add **allowances** and **deductions**, each for every month from now on or for this month only.
- See the calculation: gross pay is basic salary + allowances + overtime, and net salary is gross pay - deductions.
- **Print** the salary slip.
- **Finalize** the slip, which locks its amounts so later pay changes do not affect it. A finalized slip can be reopened.

The slip is A5 width and is A5 height when everything fits. With more lines, the page grows taller so that every allowance and deduction is listed. To keep this size, choose **Save as PDF** or a printer that accepts custom paper sizes, and set scale to 100%. A printer loaded with fixed A5 paper may continue a long slip onto a second sheet.

Basic salary is not reduced automatically for absence or for joining mid-month. Add a deduction when one is needed.

## Roles and permissions

| Access | Super Admin | Admin | Manager | Attendance Manager | Viewer |
|---|---|---|---|---|---|
| Dashboard | Yes | Yes | Yes | Yes | Yes |
| Reports and exports (without pay figures) | Yes | Yes | Yes | Yes | Yes |
| View attendance | Yes | Yes | Yes | Yes | No |
| Correct attendance, record leave | Yes | Yes | No | Yes | No |
| Add and cancel overtime | Yes | Yes | Yes | No | No |
| Salary, allowances, slips, OT rates, pay reports | Yes | Yes | No | No | No |
| Manage employees | Yes | Yes | No | No | No |
| Manage shifts and assignments | Yes | Yes | No | No | No |
| Delete shifts | Yes | Yes | No | No | No |
| Manage administrators and roles | Yes | No | No | No | No |
| System settings | Yes | No | No | No | No |
| Backup and restore | Yes | No | No | No | No |
| Audit log | Yes | No | No | No | No |

Super Admins can change what each other role can do under **User Access Management > Roles & Permissions**. Super Admin itself always has full access, and at least one active Super Admin must remain.

## Reports and Excel export

On **Attendance Reports**, use **Excel**, **CSV**, **JSON** or **Print / PDF** to export the report on screen.

To get everything at once, choose **Export All Reports**:

1. Choose a date range: today, yesterday, this or last week, this or last month, or custom dates.
2. Optionally narrow it to selected people, an employee type, a department or a shift.
3. Choose which reports to include. All are ticked by default, and pay reports appear only for people allowed to see pay.
4. Choose **Build & Download**.

The Excel workbook has an **Overview** sheet showing the filters used, then one sheet per report. Clock-in and clock-out each have their own date and time columns, which matters when a shift runs past midnight. Dates and durations are real Excel values, so they can be sorted, filtered and added up. You can also download the reports as a ZIP of CSV files or as JSON.

## Backup and restore

Go to **Backup & Restore** in the admin panel, available to Super Admins.

- **Create & Download Backup** saves a copy in the `backups` folder and downloads it.
- **Export Data** gives employees and attendance as CSV or JSON, and the system configuration as JSON.
- **Restore** replaces all current data from an uploaded or saved backup. The file is checked first, and a safety copy of the current data is saved beforehand as `timora-pre-restore-*.db`.

Your data is in `database/timora.db`. Git ignores this file and the `backups` folder, so **pushing to GitHub does not back up your data**. Keep copies of your backups on another drive.

## Troubleshooting

`start-timora.bat` and `run.py` explain most problems on screen. Common ones:

| Problem | What to do |
|---|---|
| Python could not be installed | Check your internet connection, then install from [python.org](https://www.python.org/downloads/) with **Add python.exe to PATH** ticked, and run the launcher again |
| Components could not be installed | Check your internet connection. On a company network, ask IT to allow `pypi.org` and `files.pythonhosted.org`. Details are in `logs/install.log` |
| Some files are missing | Extract the whole ZIP first, then run the launcher from the extracted folder |
| Another program is using port 5000 | Close that program, or change `port` in `config/settings.json` to another number such as `5050` |
| Timora is already running | The launcher opens the browser on the running copy. To restart, close the first window, then run the launcher again |
| Not allowed to write to the folder | Move the Timora folder to your Desktop or Documents |
| The clock shows the wrong time | Set **Business Timezone** in **System Settings** |
| An employee shows NOT SCHEDULED | Check that they have a shift assigned and that today is one of its working days, then check for holidays, leave and whether the shift is active |
| An employee is missing from the dropdown | Inactive employees are not listed. Reactivate them under **Employees** |
| Too many failed logins | The account locks for 15 minutes by default. Wait, or [reset the password](#default-login) |
| The connection-lost banner appears | The server is unreachable, and nothing is recorded until it returns. Check that the Timora window is still open and the network is working |

If startup still fails, the details may be in `logs/timora-error.log`.

## Project structure

```
├── start-timora.bat      One-click Windows launcher
├── run.py                Starts the server
├── manage.py             Maintenance commands
├── requirements.txt      Required components
├── backend/              Flask application
│   ├── app.py            Application setup, pages, error handling
│   ├── database.py       SQLite schema, transactions, audit log
│   ├── auth.py           Login, sessions, roles, administrators
│   ├── schedule.py       Works out who is scheduled and what they can do now
│   ├── attendance.py     Clocking, records, corrections, leave
│   ├── employees.py      Employee records and ID generation
│   ├── shifts.py         Shifts, assignments, holidays
│   ├── overtime.py       Added overtime
│   ├── payroll.py        Monthly salary, allowances, deductions, slips
│   ├── starter.py        Adds the starter employee to a new installation
│   ├── dashboard.py      Dashboard data
│   ├── reports.py        Reports and exports
│   ├── settings.py       System settings
│   └── backup.py         Backup, restore, data exports
├── frontend/             HTML, CSS and JavaScript, with no build step
├── config/
│   ├── settings.json     Server address, port and file locations
│   └── starter_data.json Employee added to a new installation
├── tests/                Automated tests
├── database/             Live database (not committed)
└── backups/              Backup files (not committed)
```

Business settings such as timezone and grace periods are stored in the database, so they are included in backups.

## For developers

**Stack:** Python, Flask, Waitress, SQLite, and plain HTML, CSS and JavaScript.

**Run the tests:**

```bash
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
```

**Maintenance commands:**

```bash
python manage.py init                       # Create the database
python manage.py reset-password <username>  # Set a new password
```

**Environment variables** override `config/settings.json`: `TIMORA_HOST`, `TIMORA_PORT`, `TIMORA_DATABASE` and `TIMORA_BACKUP_DIR`.

**API:** all communication is JSON.

```http
POST /api/attendance/clock
Content-Type: application/json

{ "employee_id": "EM001", "action": "clock_in", "request_id": "unique-id-per-attempt" }
```

```json
{
  "success": true,
  "employee_id": "EM001",
  "employee_name": "Heyshan Senaviratne",
  "action": "clock_in",
  "timestamp": "2026-10-31T09:00:00Z",
  "local_time": "09:00:00 AM",
  "status": "clocked_in",
  "attendance_status": "Present",
  "note": "On time",
  "message": "Clock-in successful"
}
```

Errors use the same format:

```json
{ "success": false, "code": "DUPLICATE_CLOCK_IN", "message": "Heyshan Senaviratne is already clocked in." }
```

Resending the same `request_id` returns the original result instead of recording a second event.

## Security notes

Timora is built for a trusted local network.

**Included:** hashed passwords, a forced change of the default password, login lockout, session timeout, server-side permission checks, parameterized SQL, protection against cross-site requests, and a permanent audit trail.

**Be aware:**

- **The attendance screen has no login, by design.** Anyone who can open it can see employee names and IDs, and can clock in or out as any employee.
- **Traffic is not encrypted.** Timora uses plain HTTP, so do not expose it to the internet. For access beyond a trusted network, put it behind an HTTPS reverse proxy or a VPN.
- **Keep private files private.** `database/`, `backups/` and `database/secret.key` hold your data and session key. Git already ignores them; do not share them.
