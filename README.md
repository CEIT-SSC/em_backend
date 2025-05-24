**Project Title:** Event Management Platform - Backend API

**1. Overview**

This Django application serves as the backend API for the AmirKabir University Scientific Chapter's Event Management Platform. It provides RESTful endpoints for all application functionalities, manages data persistence, business logic, user authentication, and payment processing integration.

**2. Key Backend Features**

* **RESTful API:** Comprehensive API built with Django REST Framework.
* **User Management:** Custom user model, JWT-based authentication, email verification (6-digit code), simplified password reset (emails a 6-digit temporary password).
* **Event & Activity Management:** Robust models and APIs for:
    * Events
    * Presentations (talks/workshops, online/offline)
    * Three distinct competition types:
        1.  `SoloCompetition`
        2.  `GroupCompetition` (standard, non-verified teams)
        3.  `VerifiedCompetition` (group-based, requires admin approval of teams and member Gov IDs)
    * `CompetitionTeam` and `TeamMembership` (with Gov ID picture support for verified competitions).
* **Cart & Order System:**
    * Shopping cart functionality for users to add multiple paid items.
    * Discount code application.
    * Order creation and management.
* **Payment Integration (Zarinpal):**
    * Handles payment requests by preparing data and redirecting to Zarinpal. [cite: 2, 3, 8, 13]
    * Processes callback from Zarinpal to verify transaction status. [cite: 14, 17, 19, 20, 26]
    * Manages `payment_status` for enrollments and team registrations.
* **Free Item Handling:** Allows direct enrollment/registration for free presentations and competitions, bypassing the cart and setting `payment_status` to "not_applicable".
* **User-Specific Features:** Endpoints for "Upcoming Activities" and order history.
* **Admin Panel:** Django Admin customized for managing all aspects of the platform, including the verification workflow for `VerifiedCompetition` teams.

**3. Technology Stack**

* **Framework:** Django, Django REST Framework
* **Database:** PostgreSQL (recommended)
* **Authentication:** JWT (e.g., `djangorestframework-simplejwt`)
* **Payment Gateway:** Zarinpal
* **Asynchronous Tasks:** Celery (recommended for email sending and post-payment processing)
* **Image Handling:** Pillow (for `ImageField`s)
