from hdbcli import dbapi
 
# Define your connection details
server_address = "98.39.20.142"
server_port = 39041  # Script server / specific instance port
user_name = "NITISH"
password = "CookieCo123"
 
try:
    # Establish the connection
    conn = dbapi.connect(
        address=server_address,
        port=server_port,
        user=user_name,
        password=password,
        # Use encrypt=True and sslValidateCertificate=True if connecting to SAP HANA Cloud / secure setups
        encrypt=False 
    )
    
    print("Connected to SAP HANA successfully!")
    
    # Create a cursor and execute a test query
    cursor = conn.cursor()
    cursor.execute("SELECT CURRENT_USER, CURRENT_TIMESTAMP FROM DUMMY")
    result = cursor.fetchone()
    print("Result:", result)
    
    # Close cursor and connection
    cursor.close()
    conn.close()
 
except dbapi.Error as e:
    print(f"HANA Connection Error: {e}")