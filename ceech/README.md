# Ceech - Your AI-Powered Productivity Coach

An intelligent, agent-based system that runs in the background to monitor your activity and help you stay focused on your goals.

## About The Project

Ceech is a personal productivity application built with a modern, distributed architecture. It uses a set of specialized "agents" to perform tasks like observing user activity, managing goals, and providing real-time feedback with the help of a local Large Language Model (LLM).

## Core Architecture

The system is built on a microservices-style architecture orchestrated with Docker Compose.

* **Observer Agent:** Watches for user activity (active window, process name, user status) and publishes events.
* **Planner Agent:** A FastAPI server with a PostgreSQL database that manages user goals.
* **Judge Agent:** Consumes events, fetches the current goal from the Planner, and uses a local LLM (Ollama) to produce a "verdict" on the activity.
* **Message Bus:** RabbitMQ is used as the central message bus for asynchronous communication between agents.

## Getting Started

To get a local copy up and running, follow these steps.

### Prerequisites

* Docker: [https://www.docker.com/get-started](https://www.docker.com/get-started)
* Docker Compose: (usually included with Docker Desktop)

### Installation

1.  Clone the repo:
    ```sh
    git clone [https://github.com/your_username/ceech.git](https://github.com/your_username/ceech.git)
    ```
2.  Navigate to the project directory:
    ```sh
    cd ceech
    ```
3.  Create a `.env` file from the example and fill in your details.
4.  Launch the application:
    ```sh
    docker compose up --build
    ```

## Current Status

The core infrastructure and the Observer/Planner agents are complete. The Judge agent is the next component in development.