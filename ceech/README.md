# Ceech - Your AI-Powered Productivity Coach

An intelligent, agent-based system that runs in the background to monitor your activity and help you stay focused on your goals.

## About The Project

Ceech is a personal productivity application built with a modern, distributed architecture. It uses a set of specialized "agents" to perform tasks like observing user activity, managing goals, and providing real-time feedback with the help of a local Large Language Model (LLM).

## Core Architecture

The system is built on a microservices-style architecture orchestrated with Docker Compose.

* **Observer Agent:** Watches for user activity (active window, process name, user status) and publishes events to a message queue.
* **Planner Agent:** A FastAPI server with a PostgreSQL database that manages user goals via a simple API.
* **Judge Agent:** Consumes events from the Observer, fetches the current goal from the Planner, and uses a local LLM (Ollama) to produce a "verdict" on the activity.
* **Message Bus:** RabbitMQ is used as the central message bus for resilient, asynchronous communication between agents.
* **Local LLM:** Ollama runs a language model locally, ensuring all sensitive data remains on your machine.

## Getting Started

To get a local copy up and running, follow these steps.

### Prerequisites

* Docker: [https://www.docker.com/get-started](https://www.docker.com/get-started)
* Docker Compose (version 2.x, included with modern Docker installations)

### Installation

1.  Clone the repository and navigate to the project directory:
    ```sh
    git clone [https://github.com/HaasChoy/ceech.git](https://github.com/HaasChoy/ceech.git)
    cd ceech
    ```
2.  Create a `.env` file to store your credentials and user information. This is critical for the application to run.
    ```
    # Your user and group IDs, used for container permissions
    UID=$(id -u)
    GID=$(id -g)

    # PostgreSQL credentials for the Planner Agent
    POSTGRES_DB=ceech_db
    POSTGRES_USER=ceech_user
    POSTGRES_PASSWORD=your_strong_password
    POSTGRES_PORT=5432
    ```
3.  Launch the application:
    ```sh
    docker compose up --build
    ```

## Current Status

The core infrastructure and the Observer/Planner agents are fully functional. The Judge agent is the next component in development.