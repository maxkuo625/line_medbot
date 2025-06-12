import mysql.connector
from config import DB_CONFIG

def get_conn():
    return mysql.connector.connect(**DB_CONFIG)
