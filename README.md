# SS Sports Auction Application

## Prerequisites

Before setting up the project, ensure the following are installed:

-   **Python 3.13**
-   **MySQL** (Create an empty database named `ss_sports`)

------------------------------------------------------------------------

# Next Steps for Setup

### 1. Create Virtual Environment

``` bash
python -m venv venv
```

### 2. Activate Virtual Environment

**Windows:**

``` bash
venv\Scripts\activate
```

**Mac/Linux:**

``` bash
source venv/bin/activate
```

### 3. Rename Settings File

Rename:

    config/settings.py.txt

To:

    config/settings.py

### 4. Modify Database Configuration

Update the database credentials inside `settings.py` to match your MySQL
setup and ensure the database name is:

    ss_sports

### 5. Install Dependencies

``` bash
pip install -r requirements.txt
```

### 6. Run Migrations

``` bash
python manage.py migrate
```

This will create all required tables inside the `ss_sports` database.

### 7. Create Superuser

``` bash
python manage.py createsuperuser
```

-   Username: `ss_admin`
-   Password: (Set the same as username if desired)

### 8. Run the Application

``` bash
python manage.py runserver
```

The application will run on:

    http://127.0.0.1:8000/

------------------------------------------------------------------------

# Inside the Application

### Landing Page

After running the server, you will see the landing page.

### Auction Login

-   Navigate to the **Auction Page**
-   Login using your superuser credentials (`ss_admin`)

### Create Tournament

-   Create a Tournament from the dashboard.

### Add Teams

-   Add Teams to the Tournament.

### Player Registration

-   From the Registration Page, add players to the tournament.

### Start Auction

-   Navigate to the Tournament Page.
-   Click on **Auction** to start the player auction process.

------------------------------------------------------------------------

# Application Flow Summary

1.  Create Tournament\
2.  Add Teams\
3.  Register Players\
4.  Start Auction

------------------------------------------------------------------------

🚀 Your SS Sports Auction system is now ready to use!
