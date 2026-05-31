import socket
import sys
# Ensure imports from utils directory work properly
sys.path.insert(0, ".")

from utils.protocol import receive_message, send_text
from utils.types import HeaderCode

def main():
    print("[CLIENT] Connecting to server at 127.0.0.1:9999...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    try:
        sock.connect(("127.0.0.1", 9999))
        print("[CLIENT] Connected! Sending HeaderCode.REQUEST_IP: 'alice'...")
        
        # 1. Send the message to the server
        send_text(sock, HeaderCode.REQUEST_IP, "alice")
        
        # 2. Call receive_message and print it
        msg = receive_message(sock)
        print(f"[CLIENT] Received message from server: {msg}")
        
    except Exception as e:
        print(f"[CLIENT] Error: {e}")
    finally:
        sock.close()
        print("[CLIENT] Test complete. Client socket closed.")

if __name__ == "__main__":
    main()
