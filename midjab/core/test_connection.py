# core/test_connection.py
from core.db import init_indexes, test_connection

if __name__ == "__main__":
    init_indexes()
    test_connection()
