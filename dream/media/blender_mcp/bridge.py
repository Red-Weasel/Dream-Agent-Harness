# Derived from MCP for Blender (mcp-for-blender 2.0.4, blender_mcp/server.py),
# Copyright (c) 2025 Siddharth Ahuja, MIT License -- see LICENSE in this folder.
# Upstream: https://github.com/ahujasid/blender-mcp (PyPI: mcp-for-blender)
#
# The stdio MCP bridge Dream's live Blender runs inside its sandbox (DREAM-109). Changes:
#   - kept: BlenderConnection (verbatim) and the tools get_scene_info, get_object_info,
#     get_viewport_screenshot, execute_blender_code, describe_node_type, bpy_api_lookup
#     and export_scene, whose bodies call the add-on exactly as upstream does;
#   - removed: telemetry, trajectory recording, the consent prompt, the add-on
#     installer and handshake, safe mode, the asset-library tools (Poly Haven, Sketchfab,
#     Poly Pizza, Hyper3D Rodin, Hunyuan3D), get_addon_status, disable_telemetry,
#     record_trajectory_feedback, the asset_creation_strategy prompt and the CLI;
#   - removed from every tool: the `user_prompt` argument, which upstream sends to its
#     telemetry service; nothing here reads or sends the owner's words anywhere;
#   - get_blender_connection() asks Dream's supervisor (live.py) for Blender first, so
#     Blender starts on first use and a Blender that was closed is started again; the
#     call that opened it says so at the top of its result (_with_note);
#   - the address is 127.0.0.1:9876 inside the sandbox's own network namespace;
#   - FastMCP logs warnings and errors only.

import json
import logging
import os
import socket
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP, Image

logger = logging.getLogger("BlenderMCPServer")



@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket = None  # Changed from 'socket' to 'sock' to avoid naming conflict
    # Serializes send+receive so two commands can never interleave on one socket.
    # Without this, a second command's response can be read as the first's, and
    # the stream stays desynced until the 180s timeout fires.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def connect(self) -> bool:
        """Connect to the Blender addon socket server"""
        if self.sock:
            return True
            
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {str(e)}")
            self.sock = None
            return False
    
    def disconnect(self):
        """Disconnect from the Blender addon"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Blender: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        # Use a consistent timeout value that matches the addon's timeout
        sock.settimeout(180.0)  # Match the addon's timeout
        
        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        # If we get an empty chunk, the connection might be closed
                        if not chunks:  # If we haven't received anything yet, this is an error
                            raise Exception("Connection closed before receiving any data")
                        break
                    
                    chunks.append(chunk)
                    
                    # Check if we've received a complete JSON object
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        # If we get here, it parsed successfully
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    # If we hit a timeout during receiving, break the loop and try to use what we have
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise  # Re-raise to be handled by the caller
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise
            
        # If we get here, we either timed out or broke out of the loop
        # Try to use what we have
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                # Try to parse what we have
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                # If we can't parse it, it's incomplete
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Blender and return the response"""
        # Hold the lock across send+receive: the response is matched to the
        # command purely by ordering on the stream, so overlapping calls would
        # hand each other's responses back.
        with self._lock:
            return self._send_command_locked(command_type, params)

    def _send_command_locked(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")

        command = {
            "type": command_type,
            "params": params or {}
        }

        try:
            # Log the command being sent
            logger.info(f"Sending command: {command_type} with params: {params}")
            
            # Send the command
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")
            
            # Set a timeout for receiving - use the same timeout as in receive_full_response
            self.sock.settimeout(180.0)  # Match the addon's timeout
            
            # Receive the response using the improved receive_full_response method
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")
            
            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")
            
            if response.get("status") == "error":
                logger.error(f"Blender error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Blender"))
            
            return response.get("result", {})
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Blender")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            # Just invalidate the current socket so it will be recreated next time
            self.sock = None
            raise Exception("Timeout waiting for Blender response - try simplifying your request. If Blender is running headless (blender -b), commands never execute; run Blender with a GUI or via 'xvfb-run -a blender' instead")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise Exception(f"Connection to Blender lost: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Blender: {str(e)}")
            # Try to log what was received
            if 'response_data' in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            raise Exception(f"Invalid response from Blender: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Blender: {str(e)}")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            self.sock = None
            raise Exception(f"Communication error with Blender: {str(e)}")



mcp = FastMCP("BlenderMCP", log_level="WARNING")  # Dream: no per-request INFO lines on Dream's stderr

# Dream: set by live.py. ensure_blender() returns (host, port, generation) once Blender is
# listening, starting it when it is not running; the generation changes with every new
# Blender. opened_note(first) is what a tool says when its call opened Blender.
ensure_blender = None
opened_note = None

_blender_connection = None
_connected_generation = None
_seen_generation = 0
_pending_note = ""


def _take_note() -> str:
    """The note for the call that opened Blender, once; "" otherwise."""
    global _pending_note
    note, _pending_note = _pending_note, ""
    return note


def _with_note(text: str) -> str:
    note = _take_note()
    return f"[{note}]\n{text}" if note else text


def get_blender_connection():
    """Get or create the connection to the live Blender, starting Blender when needed."""
    global _blender_connection, _connected_generation, _seen_generation, _pending_note
    host, port, generation = ensure_blender()
    if generation != _seen_generation:
        _pending_note = opened_note(_seen_generation == 0)
        _seen_generation = generation
    if _blender_connection is not None and generation != _connected_generation:
        _blender_connection.disconnect()  # a new Blender: the old socket is dead
        _blender_connection = None
    if _blender_connection is not None and _blender_connection.sock is not None:
        return _blender_connection
    if _blender_connection is None:
        _blender_connection = BlenderConnection(host=host, port=port)
    if not _blender_connection.connect():
        _blender_connection = None
        raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
    _connected_generation = generation
    return _blender_connection


@mcp.tool()
async def get_scene_info() -> str:
    """Get detailed information about the current Blender scene"""
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_scene_info")
        return _with_note(json.dumps(result, indent=2))
    except Exception as e:
        logger.error(f"Error getting scene info from Blender: {str(e)}")
        return _with_note(f"Error getting scene info: {str(e)}")


@mcp.tool()
async def get_object_info(object_name: str) -> str:
    """
    Get detailed information about a specific object in the Blender scene.

    Parameters:
    - object_name: The name of the object to get information about
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_object_info", {"name": object_name})
        return _with_note(json.dumps(result, indent=2))
    except Exception as e:
        logger.error(f"Error getting object info from Blender: {str(e)}")
        return _with_note(f"Error getting object info: {str(e)}")


@mcp.tool()
def get_viewport_screenshot(max_size: int = 1000):
    """
    Capture a screenshot of the current Blender 3D viewport.

    Parameters:
    - max_size: Maximum size in pixels for the largest dimension (default: 1000)

    Returns the screenshot as an Image.
    """
    try:
        blender = get_blender_connection()

        # Create temp file path (the sandbox's private /tmp, shared with Blender)
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"blender_screenshot_{os.getpid()}.png")

        result = blender.send_command("get_viewport_screenshot", {
            "max_size": max_size,
            "filepath": temp_path,
            "format": "png"
        })

        if "error" in result:
            raise Exception(result["error"])

        if not os.path.exists(temp_path):
            raise Exception("Screenshot file was not created")

        # Read the file
        with open(temp_path, 'rb') as f:
            image_bytes = f.read()

        # Delete the temp file
        os.remove(temp_path)

        note = _take_note()
        return [f"[{note}]", Image(data=image_bytes, format="png")] if note else Image(data=image_bytes, format="png")

    except Exception as e:
        logger.error(f"Error capturing screenshot: {str(e)}")
        note = _take_note()
        raise Exception((f"[{note}] " if note else "") + f"Screenshot failed: {str(e)}")


@mcp.tool()
async def execute_blender_code(code: str) -> str:
    """
    Execute arbitrary Python code in Blender. Make sure to do it step-by-step by breaking it into smaller chunks.
    Blender runs in Dream's sandbox: files persist only inside the workspace (its working directory), and there is no network.

    Parameters:
    - code: The Python code to execute
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        result = blender.send_command("execute_code", {"code": code})
        return _with_note(f"Code executed successfully: {result.get('result', '')}")
    except Exception as e:
        logger.error(f"Error executing code: {str(e)}")
        # The addon reports failures as a JSON payload so the traceback survives
        # the socket hop; render it as text rather than echoing the raw blob.
        try:
            detail = json.loads(str(e))
            traceback_text = detail["traceback"]
        except (ValueError, KeyError, TypeError):
            return _with_note(f"Error executing code: {str(e)}")
        return _with_note(f"Error executing code: {detail.get('exception_type', 'Error')}: {detail.get('message', '')}\n\n{traceback_text}")


@mcp.tool()
async def describe_node_type(bl_idname: str, property_overrides: Dict[str, Any] = None) -> str:
    """
    Look up the property and socket schema of a Blender node type, without touching the current scene.

    Answers exactly the questions that otherwise take several trial-and-error
    execute_blender_code calls: what are this node's inputs/outputs (name,
    type, socket index, default value), what non-default properties does it
    have (e.g. data_type, blend_type, sky_type), and what enum values are
    valid for each. Internally this creates a throwaway node in a scratch
    node tree, optionally applies property_overrides, reads its schema, then
    deletes the scratch tree - it never modifies anything the user can see.

    Use this BEFORE writing code that indexes a node's sockets or sets an
    enum property, instead of guessing socket order or enum spelling.

    Parameters:
    - bl_idname: The node's bl_idname, e.g. "ShaderNodeMix", "ShaderNodeTexSky", "ShaderNodeBsdfPrincipled".
    - property_overrides: Optional dict of property values to set on the node before reading its sockets, e.g. {"data_type": "RGBA"} for a Mix node. Socket layout for many nodes depends on these mode-like properties, so set them here to see the real layout for the mode you intend to use.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("describe_node_type", {
            "bl_idname": bl_idname,
            "property_overrides": property_overrides or {},
        })
        return _with_note(json.dumps(result, indent=2))
    except Exception as e:
        logger.error(f"Error describing node type {bl_idname}: {str(e)}")
        return _with_note(f"Error describing node type '{bl_idname}': {str(e)}")


@mcp.tool()
async def bpy_api_lookup(query: str) -> str:
    """
    Structured Blender RNA/API reference lookup: types, properties, functions, and operators.

    Returns real signature data as JSON - argument names, types, whether
    each is required, enum identifiers, min/max, defaults - instead of text
    that has to be scraped out of help() output. Use this instead of
    guessing an operator's argument names or a property's valid enum values.

    Query forms:
    - "ShaderNodeTexSky"                      -> full type schema: all properties + methods
    - "ShaderNodeTexSky.sky_type"              -> one property's type, enum items, default
    - "Object.ray_cast"                        -> one method's parameters and return values
    - "bpy.ops.mesh.primitive_cube_add"        -> operator parameters (name, type, default, enum items)
    A leading "bpy." / "bpy.types." is optional and stripped automatically.
    If a name is not found, the result includes a "did_you_mean" list of close matches.

    Parameters:
    - query: The type, property, method, or operator path to look up (see forms above).
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("bpy_api_lookup", {"query": query})
        return _with_note(json.dumps(result, indent=2))
    except Exception as e:
        logger.error(f"Error looking up '{query}': {str(e)}")
        return _with_note(f"Error looking up '{query}': {str(e)}")


@mcp.tool()
async def export_scene(
    filepath: str,
    format: str = "glb",
    object_names: list[str] = None,
    selection_only: bool = False,
    apply_modifiers: bool = True,
) -> str:
    """
    Export the whole scene, the current selection, or named objects to a GLB or FBX file on disk,
    so another application (a game engine, a viewer, a converter) can pick it up.

    Parameters:
    - filepath: Path of the file to write (.glb or .fbx), inside the workspace. Parent folders are created.
    - format: "glb" (default; keeps PBR materials, emission, skins, shape keys, animation) or "fbx".
    - object_names: Export only these objects (children included). Omit for selection_only or the whole scene.
    - selection_only: Export what is currently selected in Blender (ignored when object_names is given).
    - apply_modifiers: Bake modifiers on export. Use false for rigged / shape-key meshes.

    Returns JSON with path, bytes, selection_only and the exported object names.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("export_scene", {
            "filepath": filepath,
            "format": format,
            "object_names": object_names,
            "selection_only": selection_only,
            "apply_modifiers": apply_modifiers,
        })
        return _with_note(json.dumps(result) if isinstance(result, dict) else result)
    except Exception as e:
        logger.error(f"Error exporting scene: {str(e)}")
        return _with_note(f"Error exporting scene: {str(e)}")
