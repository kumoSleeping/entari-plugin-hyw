from typing import List, Dict, Any
from starlette.websockets import WebSocket
import json
import asyncio
from loguru import logger

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"New WS connection. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WS disconnected. Remaining: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast a structured message to all clients."""
        if not self.active_connections:
            return
            
        json_msg = json.dumps(message)
        to_remove = []
        
        for connection in self.active_connections:
            try:
                await connection.send_text(json_msg)
            except Exception as e:
                logger.error(f"Error sending to WS: {e}")
                to_remove.append(connection)
                
        for conn in to_remove:
            self.disconnect(conn)

    async def send_personal_message(self, message: Dict[str, Any], websocket: WebSocket):
        try:
            await websocket.send_text(json.dumps(message))
        except Exception as e:
            logger.error(f"Error sending personal WS message: {e}")
            self.disconnect(websocket)

manager = ConnectionManager()
