"""
External HTTP Server Routes for entari_plugin_hyw.

Provides REST API endpoints that mirror the chat commands:
- POST /hyw/query   - Text query, returns markdown JSON
- POST /hyw/query_r - Text query, returns rendered JPEG image
- GET  /hyw/health  - Health check

Requires: entari_plugin_server (optional dependency)
"""

import tempfile
import os
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response


def setup_server_routes(conf, get_hyw_core, resolve_model_name, QueryRequest, get_content_renderer, version: str):
    """
    Setup HTTP routes if entari_plugin_server is available and enabled.

    Args:
        conf: HywConfig instance
        get_hyw_core: Function to get HywCore instance
        resolve_model_name: Function to resolve model aliases
        QueryRequest: QueryRequest class from hyw_core
        get_content_renderer: Function to get content renderer
        version: Plugin version string
    """
    if not conf.enable_server:
        return

    try:
        from entari_plugin_server import add_route, add_websocket_route
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.websockets import WebSocket, WebSocketDisconnect
    except ImportError:
        try:
             # Fallback if add_websocket_route is not exported
             from entari_plugin_server import add_route
             add_websocket_route = None
             from starlette.requests import Request
             from starlette.responses import JSONResponse, Response
             from starlette.websockets import WebSocket, WebSocketDisconnect
        except ImportError:
            logger.warning(
                "enable_server=True but entari_plugin_server is not installed. "
                "Install it with: pip install entari-plugin-server"
            )
            return

    logger.info(f"Setting up external server routes at {conf.server_path}")

    def _check_token(request: Request) -> bool:
        """Validate token if configured."""
        if not conf.server_token:
            return True
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:] == conf.server_token
        return False

    async def _parse_request(request: Request):
        """Parse and validate request body."""
        try:
            body = await request.json()
        except Exception:
            return None, JSONResponse({"status": "error", "error": "Invalid JSON"}, status_code=400)

        query = body.get("query", "")
        if not query:
            return None, JSONResponse({"status": "error", "error": "Missing 'query' field"}, status_code=400)

        return body, None

    async def _execute_query(body, conf):
        """Execute query and return response object."""
        query = body.get("query", "")
        images = body.get("images", [])
        model = body.get("model")
        history = body.get("conversation_history", [])

        core = get_hyw_core()

        # Resolve model name if provided
        if model:
            resolved, _ = resolve_model_name(model, conf.models)
            if resolved:
                model = resolved

        request_obj = QueryRequest(
            user_input=query,
            images=images,
            conversation_history=history,
            model_name=model,
        )

        return await core.query_agent(request_obj, output_path=None)

    @add_route(f"{conf.server_path}/health", methods=["GET"])
    async def health_check(request: Request):
        """Health check endpoint."""
        return JSONResponse({
            "status": "ok",
            "version": version,
            "plugin": "entari_plugin_hyw"
        })

    @add_route(f"{conf.server_path}/query", methods=["POST"])
    async def handle_query(request: Request):
        """
        Handle query from external clients - returns markdown text.

        Request body:
            {
                "query": str,
                "images": list[str] (optional, base64 encoded images),
                "model": str (optional, model name or alias),
                "conversation_history": list[dict] (optional)
            }

        Response:
            {
                "status": "ok" | "error" | "refused",
                "content": str (markdown response),
                "total_time": float,
                "stages_used": list,
                "error": str (if status == "error")
            }
        """
        if not _check_token(request):
            return JSONResponse({"status": "error", "error": "Unauthorized"}, status_code=401)

        body, error_response = await _parse_request(request)
        if error_response:
            return error_response

        try:
            response = await _execute_query(body, conf)

            if response.should_refuse:
                return JSONResponse({
                    "status": "refused",
                    "content": response.refuse_reason or "Refused",
                })

            if not response.success:
                return JSONResponse({
                    "status": "error",
                    "error": response.error or "Unknown error",
                })

            return JSONResponse({
                "status": "ok",
                "content": response.content,
                "total_time": response.total_time,
                "stages_used": response.stages_used,
            })

        except Exception as e:
            logger.exception(f"Query handler error: {e}")
            return JSONResponse({"status": "error", "error": str(e)}, status_code=500)

    @add_route(f"{conf.server_path}/query_r", methods=["POST"])
    async def handle_query_render(request: Request):
        """
        Handle query from external clients - returns rendered JPEG image.

        Request body:
            {
                "query": str,
                "images": list[str] (optional, base64 encoded images),
                "model": str (optional, model name or alias),
                "conversation_history": list[dict] (optional)
            }

        Response:
            Content-Type: image/jpeg
            Body: JPEG image bytes

        On error:
            Content-Type: application/json
            {"status": "error", "error": "..."}
        """
        if not _check_token(request):
            return JSONResponse({"status": "error", "error": "Unauthorized"}, status_code=401)

        body, error_response = await _parse_request(request)
        if error_response:
            return error_response

        try:
            response = await _execute_query(body, conf)

            if response.should_refuse:
                return await _render_refuse_response(
                    response.refuse_reason or "Refused", conf.theme_color
                )

            if not response.success:
                return JSONResponse({
                    "status": "error",
                    "error": response.error or "Unknown error",
                })

            return await _render_image_response(response)

        except Exception as e:
            logger.exception(f"Query render handler error: {e}")
            return JSONResponse({"status": "error", "error": str(e)}, status_code=500)

    async def _render_image_response(response):
        """Render response as JPEG image."""
        core = get_hyw_core()
        local_renderer = await get_content_renderer()

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            output_path = tf.name

        try:
            tab_id = await local_renderer.prepare_tab()

            render_ok = await core.render(
                markdown_content=response.content,
                output_path=output_path,
                stats={"total_time": response.total_time},
                references=response.references,
                page_references=response.page_references,
                image_references=response.image_references,
                stages_used=response.stages_used,
                tab_id=tab_id
            )

            if render_ok and os.path.exists(output_path):
                with open(output_path, "rb") as f:
                    img_bytes = f.read()
                return Response(content=img_bytes, media_type="image/jpeg")
            else:
                return JSONResponse({"status": "error", "error": "Render failed"}, status_code=500)

        finally:
            if os.path.exists(output_path):
                os.remove(output_path)

    async def _render_refuse_response(reason: str, theme_color: str):
        """Render refuse message as JPEG image."""
        from .misc import render_refuse_answer

        local_renderer = await get_content_renderer()

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            output_path = tf.name

        try:
            tab_id = await local_renderer.prepare_tab()

            render_ok = await render_refuse_answer(
                renderer=local_renderer,
                output_path=output_path,
                reason=reason,
                theme_color=theme_color,
                tab_id=tab_id,
            )

            if render_ok and os.path.exists(output_path):
                with open(output_path, "rb") as f:
                    img_bytes = f.read()
                return Response(content=img_bytes, media_type="image/jpeg")
            else:
                return JSONResponse({"status": "error", "error": "Render failed"}, status_code=500)

        finally:
            if os.path.exists(output_path):
                os.remove(output_path)

    if add_websocket_route:
        @add_websocket_route(f"{conf.server_path}/ws")
        async def handle_ws(websocket: WebSocket):
            from .ws_manager import manager
            await manager.connect(websocket)
            try:
                while True:
                    data = await websocket.receive_json()
                    query = data.get("query")
                    model = data.get("model")
                    history = data.get("conversation_history", [])
                    
                    if query:
                        async def ws_event_callback(event):
                             await manager.send_personal_message(event, websocket)
                        
                        core = get_hyw_core()
                        
                        # Resolve model name if provided
                        if model:
                            resolved, _ = resolve_model_name(model, conf.models)
                            if resolved:
                                model = resolved

                        request_obj = QueryRequest(
                            user_input=query,
                            conversation_history=history,
                            model_name=model,
                            event_callback=ws_event_callback
                        )
                        
                        await core.query_agent(request_obj)
                        
            except WebSocketDisconnect:
                manager.disconnect(websocket)
            except Exception as e:
                logger.error(f"WS Error: {e}")
                manager.disconnect(websocket)
        logger.info(f"External WS route registered: {conf.server_path}/ws")

    logger.info(f"External server routes registered: {conf.server_path}/health, {conf.server_path}/query, {conf.server_path}/query_r")
