import socket
import sys
# Ensure imports from utils directory work properly
sys.path.insert(0, ".")

from utils.protocol import receive_message, send_error
from utils.exceptions import RequestException, ExceptionCodes

def main():
    print("[SERVER] Starting test server on 127.0.0.1:9999...")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 9999))
    server.listen(1)
    
    print("[SERVER] Waiting for client connection...")
    conn, addr = server.accept()
    print(f"[SERVER] Client connected from {addr}")
    
    try:
        # 1. Receive message from client and print it
        msg = receive_message(conn)
        print(f"[SERVER] Received message from client: {msg}")
        
        # 2. Reply to client by transmitting a simulated RequestException
        print("[SERVER] Simulating server error: sending ExceptionCodes.NOT_FOUND ('nope')")
        exc = RequestException(msg="nope", code=ExceptionCodes.NOT_FOUND)
        send_error(conn, exc)
        
    except Exception as e:
        print(f"[SERVER] Error: {e}")
    finally:
        conn.close()
        server.close()
        print("[SERVER] Test complete. Server socket closed.")

if __name__ == "__main__":
    main()
