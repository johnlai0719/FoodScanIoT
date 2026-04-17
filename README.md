# FoodScanIoT - Food Safety Information Summary and Risk Notification System

This system implements a collaborative architecture between Cloud and Fog nodes to provide intelligent food safety analysis. The project uses a monorepo structure to integrate server-side services, mobile applications, and database management components.

## Project Structure

The project is organized into the following core directories:

### server/
Cloud central server (FastAPI + Gemini AI). Responsible for deep analysis, AI interpretation, and large-scale data storage. Includes database initialization and maintenance scripts.

### fog/
Fog edge node (FastAPI + SQLite). Handles real-time caching and risk prediction in environments with limited connectivity.

### APP/
Primary mobile application (React Native / Expo). Features camera-based scanning and personalized food safety reports.

### shared/
Common TypeScript type definitions and constants used across both client and server applications.

## Development and Deployment

### Backend Services (Docker)
To start the database and Cloud server, navigate to the server directory and execute:
```bash
docker-compose up -d
```

### Environment Configuration
Ensure a `.env` file exists in the server directory with the following variables:
- GEMINI_API_KEY: Google Gemini AI API Key.
- DATABASE_URL: MySQL connection string (automatically configured by docker-compose).

## Technical Specifications
- Frontend: React Native (Expo), TailwindCSS, NativeWind
- Backend: FastAPI (Python)
- AI: Google Gemini AI
- Database: MySQL (Cloud), SQLAlchemy (ORM)
