"""Proxmox VE MCP Server.

Manages a Proxmox VE cluster via the REST API using token authentication.
Tested with Proxmox VE 9.1.9.

Configuration is loaded from environment variables (typically via .env):
    PROXMOX_HOST        - Proxmox host or IP
    PROXMOX_PORT        - API port (default: 8006)
    PROXMOX_USER        - User (e.g. root@pam)
    PROXMOX_TOKEN_NAME  - Token ID
    PROXMOX_TOKEN_VALUE - Token secret UUID
    PROXMOX_VERIFY_SSL  - "true" or "false" (default: false)
    PROXMOX_TIMEOUT     - HTTP timeout seconds (default: 30)
"""
from __future__ import annotations

import json
import os
import sys
from enum import Enum
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()

PROXMOX_HOST = os.getenv("PROXMOX_HOST", "").strip()
PROXMOX_PORT = os.getenv("PROXMOX_PORT", "8006").strip()
PROXMOX_USER = os.getenv("PROXMOX_USER", "").strip()
PROXMOX_TOKEN_NAME = os.getenv("PROXMOX_TOKEN_NAME", "").strip()
PROXMOX_TOKEN_VALUE = os.getenv("PROXMOX_TOKEN_VALUE", "").strip()
PROXMOX_VERIFY_SSL = os.getenv("PROXMOX_VERIFY_SSL", "false").lower() == "true"
PROXMOX_TIMEOUT = float(os.getenv("PROXMOX_TIMEOUT", "30"))

mcp = FastMCP("proxmox_mcp")


def _require_config() -> Optional[str]:
    missing = []
    if not PROXMOX_HOST:
        missing.append("PROXMOX_HOST")
    if not PROXMOX_USER:
        missing.append("PROXMOX_USER")
    if not PROXMOX_TOKEN_NAME:
        missing.append("PROXMOX_TOKEN_NAME")
    if not PROXMOX_TOKEN_VALUE:
        missing.append("PROXMOX_TOKEN_VALUE")
    if missing:
        return f"Error: Missing env vars: {', '.join(missing)}"
    return None


def _base_url() -> str:
    return f"https://{PROXMOX_HOST}:{PROXMOX_PORT}/api2/json"


def _auth_header() -> dict[str, str]:
    return {
        "Authorization": (
            f"PVEAPIToken={PROXMOX_USER}!{PROXMOX_TOKEN_NAME}={PROXMOX_TOKEN_VALUE}"
        )
    }


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_base_url(),
        headers=_auth_header(),
        verify=PROXMOX_VERIFY_SSL,
        timeout=PROXMOX_TIMEOUT,
    )



def _format_http_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text[:300]
        if status == 401:
            return "Error: Authentication failed. Check PROXMOX_TOKEN_VALUE."
        if status == 403:
            return f"Error: Permission denied. Token lacks privileges. {body}"
        if status == 404:
            return f"Error: Resource not found. {body}"
        return f"Error: HTTP {status}: {body}"
    if isinstance(exc, httpx.ConnectError):
        return f"Error: Cannot connect to {PROXMOX_HOST}:{PROXMOX_PORT}"
    if isinstance(exc, httpx.TimeoutException):
        return f"Error: Request timed out after {PROXMOX_TIMEOUT}s"
    return f"Error: {type(exc).__name__}: {exc}"


async def _get(path: str, params: Optional[dict] = None) -> Any:
    async with _client() as c:
        r = await c.get(path, params=params)
        r.raise_for_status()
        return r.json().get("data")


async def _post(path: str, data: Optional[dict] = None) -> Any:
    async with _client() as c:
        r = await c.post(path, data=data or {})
        r.raise_for_status()
        return r.json().get("data")



class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FormatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'.",
    )


class NodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., description="Node name (e.g., 'pve')", min_length=1)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'.",
    )


class VMInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., description="Node name (e.g., 'pve')", min_length=1)
    vmid: int = Field(..., description="VM or container ID", ge=100, le=999999999)
    vm_type: str = Field(
        default="qemu",
        description="VM type: 'qemu' for VMs, 'lxc' for containers",
        pattern="^(qemu|lxc)$",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'.",
    )



class VMActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., description="Node name", min_length=1)
    vmid: int = Field(..., description="VM or container ID", ge=100)
    vm_type: str = Field(
        default="qemu", description="VM type", pattern="^(qemu|lxc)$"
    )
    confirm: bool = Field(
        default=False,
        description="Must be true to execute. Only set after explicit user confirmation.",
    )
    reason: Optional[str] = Field(
        default=None, description="Optional note about why", max_length=200
    )


class SnapshotCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1)
    vmid: int = Field(..., ge=100)
    vm_type: str = Field(default="qemu", pattern="^(qemu|lxc)$")
    snapname: str = Field(
        ..., description="Snapshot name (alphanumeric, dash, underscore)",
        min_length=1, max_length=40, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"
    )
    description: Optional[str] = Field(default=None, max_length=200)
    confirm: bool = Field(default=False)


class SnapshotRollbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1)
    vmid: int = Field(..., ge=100)
    vm_type: str = Field(default="qemu", pattern="^(qemu|lxc)$")
    snapname: str = Field(..., min_length=1, max_length=40)
    confirm: bool = Field(default=False)


class BackupCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1)
    vmid: int = Field(..., ge=100)
    storage: str = Field(default="local", description="Storage for backup")
    mode: str = Field(
        default="snapshot",
        description="Backup mode: snapshot, suspend, or stop",
        pattern="^(snapshot|suspend|stop)$",
    )
    compress: str = Field(
        default="zstd",
        description="Compression: none, lzo, gzip, zstd",
        pattern="^(none|lzo|gzip|zstd)$",
    )
    confirm: bool = Field(default=False)


class VMResizeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., description="Node name", min_length=1)
    vmid: int = Field(..., description="VM or container ID", ge=100)
    vm_type: str = Field(
        default="qemu", description="VM type", pattern="^(qemu|lxc)$"
    )
    memory_mb: Optional[int] = Field(
        default=None,
        description="New RAM size in MB (e.g. 4096 for 4 GB). Omit to keep current.",
        ge=16,
        le=1048576,
    )
    cores: Optional[int] = Field(
        default=None,
        description="New CPU core count. Omit to keep current.",
        ge=1,
        le=256,
    )
    confirm: bool = Field(
        default=False,
        description="Must be true to execute. Only set after explicit user confirmation.",
    )
    reason: Optional[str] = Field(
        default=None, description="Optional note about why", max_length=200
    )


def _missing_confirm(action: str) -> str:
    return (
        f"Refused: '{action}' requires confirm=true. "
        "Ask the user to confirm, then retry with confirm=true."
    )


def _fmt_bytes(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    if n < 1024:
        return f"{n:.0f} B"
    for unit in ["KB", "MB", "GB", "TB"]:
        n /= 1024
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} PB"


def _fmt_uptime(secs: Any) -> str:
    try:
        s = int(secs)
    except (TypeError, ValueError):
        return "?"
    if s <= 0:
        return "-"
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


def _status_icon(status: str) -> str:
    return {"running": "🟢", "online": "🟢", "stopped": "🔴", "offline": "🔴"}.get(
        status, "⚪"
    )



@mcp.tool(
    name="proxmox_list_nodes",
    annotations={
        "title": "List Proxmox Nodes",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def proxmox_list_nodes(params: FormatInput) -> str:
    """List all nodes in the Proxmox cluster with status and resource usage.

    Returns:
        str: For each node: name, status, uptime, CPU, memory.
    """
    cfg = _require_config()
    if cfg:
        return cfg
    try:
        nodes = await _get("/nodes")
    except Exception as exc:
        return _format_http_error(exc)

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(nodes, indent=2, default=str)

    if not nodes:
        return "_No nodes found._"

    lines = ["## Proxmox Cluster Nodes", ""]
    for n in nodes:
        icon = _status_icon(n.get("status", "?"))
        cpu_pct = (n.get("cpu") or 0) * 100
        mem_used = _fmt_bytes(n.get("mem", 0))
        mem_total = _fmt_bytes(n.get("maxmem", 0))
        uptime = _fmt_uptime(n.get("uptime", 0))
        lines.append(
            f"- {icon} **{n.get('node')}** — {n.get('status')} — "
            f"uptime: {uptime}, CPU: {cpu_pct:.1f}%, "
            f"Mem: {mem_used}/{mem_total}"
        )
    return "\n".join(lines)



@mcp.tool(
    name="proxmox_get_node_status",
    annotations={
        "title": "Get Node Status (Detailed)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def proxmox_get_node_status(params: NodeInput) -> str:
    """Get detailed status for a node: CPU, memory, disk, load average, kernel.

    Returns:
        str: Detailed node metrics in markdown or JSON.
    """
    cfg = _require_config()
    if cfg:
        return cfg
    try:
        status = await _get(f"/nodes/{params.node}/status")
    except Exception as exc:
        return _format_http_error(exc)

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(status, indent=2, default=str)

    cpu = status.get("cpuinfo", {}) or {}
    mem = status.get("memory", {}) or {}
    swap = status.get("swap", {}) or {}
    rootfs = status.get("rootfs", {}) or {}
    load = status.get("loadavg", []) or []

    lines = [f"## Node `{params.node}` Status", ""]
    lines.append(f"- **Uptime**: {_fmt_uptime(status.get('uptime', 0))}")
    lines.append(f"- **Kernel**: {status.get('kversion', '?')}")
    lines.append(f"- **PVE version**: {status.get('pveversion', '?')}")
    lines.append(
        f"- **CPU**: {cpu.get('model', '?')} — "
        f"{cpu.get('cpus', '?')} cores @ {cpu.get('mhz', '?')} MHz"
    )
    lines.append(
        f"- **CPU usage**: {(status.get('cpu') or 0) * 100:.1f}%"
    )
    lines.append(
        f"- **Memory**: {_fmt_bytes(mem.get('used'))} / "
        f"{_fmt_bytes(mem.get('total'))}"
    )
    if swap.get("total"):
        lines.append(
            f"- **Swap**: {_fmt_bytes(swap.get('used'))} / "
            f"{_fmt_bytes(swap.get('total'))}"
        )
    if rootfs.get("total"):
        lines.append(
            f"- **Root FS**: {_fmt_bytes(rootfs.get('used'))} / "
            f"{_fmt_bytes(rootfs.get('total'))}"
        )
    if load:
        lines.append(f"- **Load avg**: {', '.join(str(x) for x in load)}")
    return "\n".join(lines)



@mcp.tool(
    name="proxmox_list_vms",
    annotations={
        "title": "List All VMs and Containers",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def proxmox_list_vms(params: FormatInput) -> str:
    """List all virtual machines and LXC containers across the cluster.

    Returns:
        str: For each VM/CT: ID, name, type, node, status, CPU, memory.
    """
    cfg = _require_config()
    if cfg:
        return cfg
    try:
        vms = await _get("/cluster/resources", params={"type": "vm"})
    except Exception as exc:
        return _format_http_error(exc)

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(vms, indent=2, default=str)

    if not vms:
        return "_No VMs or containers found._"

    lines = ["## Virtual Machines and Containers", ""]
    for v in sorted(vms, key=lambda x: x.get("vmid", 0)):
        icon = _status_icon(v.get("status", "?"))
        vmtype = "📦 LXC" if v.get("type") == "lxc" else "💻 VM"
        name = v.get("name", "?")
        vmid = v.get("vmid", "?")
        node = v.get("node", "?")
        cpu = (v.get("cpu") or 0) * 100
        mem_used = _fmt_bytes(v.get("mem", 0))
        mem_total = _fmt_bytes(v.get("maxmem", 0))
        uptime = _fmt_uptime(v.get("uptime", 0))
        lines.append(
            f"- {icon} {vmtype} **{vmid}** `{name}` on `{node}` — "
            f"{v.get('status')} — uptime: {uptime}, "
            f"CPU: {cpu:.1f}%, Mem: {mem_used}/{mem_total}"
        )
    return "\n".join(lines)



@mcp.tool(
    name="proxmox_get_vm_status",
    annotations={
        "title": "Get VM/Container Detailed Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def proxmox_get_vm_status(params: VMInput) -> str:
    """Get detailed status of a specific VM or LXC container.

    Returns:
        str: Detailed runtime metrics for the VM/container.
    """
    cfg = _require_config()
    if cfg:
        return cfg
    try:
        status = await _get(
            f"/nodes/{params.node}/{params.vm_type}/{params.vmid}/status/current"
        )
    except Exception as exc:
        return _format_http_error(exc)

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(status, indent=2, default=str)

    icon = _status_icon(status.get("status", "?"))
    vmtype = "LXC Container" if params.vm_type == "lxc" else "QEMU VM"
    lines = [
        f"## {icon} {vmtype} {params.vmid} `{status.get('name', '?')}`",
        "",
        f"- **Node**: `{params.node}`",
        f"- **Status**: {status.get('status', '?')}",
        f"- **Uptime**: {_fmt_uptime(status.get('uptime', 0))}",
        f"- **CPU**: {(status.get('cpu') or 0) * 100:.1f}% of "
        f"{status.get('cpus', '?')} cores",
        f"- **Memory**: {_fmt_bytes(status.get('mem'))} / "
        f"{_fmt_bytes(status.get('maxmem'))}",
        f"- **Disk read**: {_fmt_bytes(status.get('diskread'))}",
        f"- **Disk write**: {_fmt_bytes(status.get('diskwrite'))}",
        f"- **Network in**: {_fmt_bytes(status.get('netin'))}",
        f"- **Network out**: {_fmt_bytes(status.get('netout'))}",
    ]
    if status.get("agent"):
        lines.append(f"- **Guest agent**: enabled")
    if status.get("ha", {}).get("managed"):
        lines.append(f"- **HA managed**: yes")
    return "\n".join(lines)



@mcp.tool(
    name="proxmox_list_storage",
    annotations={
        "title": "List Storage Pools",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def proxmox_list_storage(params: NodeInput) -> str:
    """List storage pools on a node with usage information.

    Returns:
        str: For each storage: name, type, usage, content types.
    """
    cfg = _require_config()
    if cfg:
        return cfg
    try:
        storages = await _get(f"/nodes/{params.node}/storage")
    except Exception as exc:
        return _format_http_error(exc)

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(storages, indent=2, default=str)

    if not storages:
        return "_No storage pools found._"

    lines = [f"## Storage on `{params.node}`", ""]
    for s in storages:
        used = s.get("used", 0)
        total = s.get("total", 0)
        pct = (used / total * 100) if total else 0
        active = "🟢" if s.get("active") else "🔴"
        lines.append(
            f"- {active} **{s.get('storage')}** ({s.get('type')}) — "
            f"{_fmt_bytes(used)}/{_fmt_bytes(total)} ({pct:.0f}%) — "
            f"content: {s.get('content', '?')}"
        )
    return "\n".join(lines)



class StorageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(..., min_length=1)
    storage: str = Field(default="local", description="Storage name")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="proxmox_list_backups",
    annotations={
        "title": "List Backups",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def proxmox_list_backups(params: StorageInput) -> str:
    """List backup files on a storage.

    Returns:
        str: For each backup: filename, VMID, size, creation time.
    """
    cfg = _require_config()
    if cfg:
        return cfg
    try:
        backups = await _get(
            f"/nodes/{params.node}/storage/{params.storage}/content",
            params={"content": "backup"},
        )
    except Exception as exc:
        return _format_http_error(exc)

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(backups, indent=2, default=str)

    if not backups:
        return f"_No backups on `{params.storage}`._"

    import datetime as _dt
    lines = [f"## Backups on `{params.storage}` (node `{params.node}`)", ""]
    for b in sorted(backups, key=lambda x: x.get("ctime", 0), reverse=True):
        volid = b.get("volid", "?")
        size = _fmt_bytes(b.get("size", 0))
        vmid = b.get("vmid", "?")
        ctime = b.get("ctime", 0)
        try:
            ts = _dt.datetime.fromtimestamp(int(ctime)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            ts = "?"
        lines.append(f"- **VM {vmid}** — {size} — {ts}  \n  `{volid}`")
    return "\n".join(lines)



@mcp.tool(
    name="proxmox_list_snapshots",
    annotations={
        "title": "List VM Snapshots",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def proxmox_list_snapshots(params: VMInput) -> str:
    """List snapshots for a specific VM or LXC container.

    Returns:
        str: For each snapshot: name, parent, description.
    """
    cfg = _require_config()
    if cfg:
        return cfg
    try:
        snaps = await _get(
            f"/nodes/{params.node}/{params.vm_type}/{params.vmid}/snapshot"
        )
    except Exception as exc:
        return _format_http_error(exc)

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(snaps, indent=2, default=str)

    if not snaps:
        return f"_No snapshots for VM {params.vmid}._"

    import datetime as _dt
    lines = [f"## Snapshots for VM {params.vmid}", ""]
    for s in snaps:
        name = s.get("name", "?")
        if name == "current":
            lines.append(f"- 📍 **current** — _you are here_")
            continue
        snaptime = s.get("snaptime", 0)
        try:
            ts = _dt.datetime.fromtimestamp(int(snaptime)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            ts = "?"
        desc = s.get("description", "").strip()
        lines.append(f"- 📸 **{name}** — {ts}" + (f"  \n  {desc}" if desc else ""))
    return "\n".join(lines)



async def _vm_action(node: str, vmid: int, vm_type: str, action: str) -> str:
    """Send a power action to a VM/CT. Returns result message."""
    try:
        task_id = await _post(
            f"/nodes/{node}/{vm_type}/{vmid}/status/{action}"
        )
    except Exception as exc:
        return _format_http_error(exc)
    return (
        f"OK: Action '{action}' on {vm_type} {vmid} accepted. "
        f"Task ID: {task_id}. "
        "Use proxmox_get_vm_status to confirm new state."
    )


@mcp.tool(
    name="proxmox_vm_start",
    annotations={
        "title": "Start VM/Container",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def proxmox_vm_start(params: VMActionInput) -> str:
    """Start a VM or LXC container. Requires confirm=true."""
    cfg = _require_config()
    if cfg:
        return cfg
    if not params.confirm:
        return _missing_confirm("proxmox_vm_start")
    return await _vm_action(params.node, params.vmid, params.vm_type, "start")


@mcp.tool(
    name="proxmox_vm_shutdown",
    annotations={
        "title": "Graceful Shutdown",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def proxmox_vm_shutdown(params: VMActionInput) -> str:
    """Gracefully shutdown a VM or LXC container via ACPI. Requires confirm=true."""
    cfg = _require_config()
    if cfg:
        return cfg
    if not params.confirm:
        return _missing_confirm("proxmox_vm_shutdown")
    return await _vm_action(params.node, params.vmid, params.vm_type, "shutdown")



@mcp.tool(
    name="proxmox_vm_stop",
    annotations={
        "title": "Force Stop VM/Container",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def proxmox_vm_stop(params: VMActionInput) -> str:
    """Force stop (pull-the-plug) a VM or container. May cause data loss. Requires confirm=true."""
    cfg = _require_config()
    if cfg:
        return cfg
    if not params.confirm:
        return _missing_confirm("proxmox_vm_stop")
    return await _vm_action(params.node, params.vmid, params.vm_type, "stop")


@mcp.tool(
    name="proxmox_vm_reboot",
    annotations={
        "title": "Reboot VM/Container",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def proxmox_vm_reboot(params: VMActionInput) -> str:
    """Reboot a VM or container (graceful then power-cycle). Requires confirm=true."""
    cfg = _require_config()
    if cfg:
        return cfg
    if not params.confirm:
        return _missing_confirm("proxmox_vm_reboot")
    return await _vm_action(params.node, params.vmid, params.vm_type, "reboot")



@mcp.tool(
    name="proxmox_create_snapshot",
    annotations={
        "title": "Create VM Snapshot",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def proxmox_create_snapshot(params: SnapshotCreateInput) -> str:
    """Create a snapshot of a VM or container. Requires confirm=true.

    Snapshot name must start with a letter; alphanumeric, dash, underscore only.
    """
    cfg = _require_config()
    if cfg:
        return cfg
    if not params.confirm:
        return _missing_confirm("proxmox_create_snapshot")
    payload = {"snapname": params.snapname}
    if params.description:
        payload["description"] = params.description
    try:
        task_id = await _post(
            f"/nodes/{params.node}/{params.vm_type}/{params.vmid}/snapshot",
            data=payload,
        )
    except Exception as exc:
        return _format_http_error(exc)
    return (
        f"OK: Snapshot '{params.snapname}' creation started for "
        f"{params.vm_type} {params.vmid}. Task: {task_id}"
    )


@mcp.tool(
    name="proxmox_rollback_snapshot",
    annotations={
        "title": "Rollback to Snapshot",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def proxmox_rollback_snapshot(params: SnapshotRollbackInput) -> str:
    """Rollback VM/container to a snapshot. Data after snapshot is lost. Requires confirm=true."""
    cfg = _require_config()
    if cfg:
        return cfg
    if not params.confirm:
        return _missing_confirm("proxmox_rollback_snapshot")
    try:
        task_id = await _post(
            f"/nodes/{params.node}/{params.vm_type}/{params.vmid}"
            f"/snapshot/{params.snapname}/rollback"
        )
    except Exception as exc:
        return _format_http_error(exc)
    return (
        f"OK: Rollback to '{params.snapname}' started for "
        f"{params.vm_type} {params.vmid}. Task: {task_id}"
    )



@mcp.tool(
    name="proxmox_create_backup",
    annotations={
        "title": "Create VM Backup",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def proxmox_create_backup(params: BackupCreateInput) -> str:
    """Create a backup of a VM/container. Requires confirm=true.

    Modes:
      - snapshot: quick backup using snapshots (minimal downtime, recommended)
      - suspend: suspends VM during backup (consistency)
      - stop: stops VM during backup (max consistency, max downtime)

    Compression: none, lzo, gzip, zstd (zstd recommended)
    """
    cfg = _require_config()
    if cfg:
        return cfg
    if not params.confirm:
        return _missing_confirm("proxmox_create_backup")
    payload = {
        "vmid": params.vmid,
        "storage": params.storage,
        "mode": params.mode,
        "compress": params.compress,
    }
    try:
        task_id = await _post(f"/nodes/{params.node}/vzdump", data=payload)
    except Exception as exc:
        return _format_http_error(exc)
    return (
        f"OK: Backup of VM {params.vmid} started on storage "
        f"`{params.storage}` (mode={params.mode}, compress={params.compress}). "
        f"Task: {task_id}. Backup runs in background; "
        "use proxmox_list_backups to verify completion."
    )


@mcp.tool(
    name="proxmox_resize_vm",
    annotations={
        "title": "Resize VM/Container RAM and/or CPU",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def proxmox_resize_vm(params: VMResizeInput) -> str:
    """Change RAM and/or CPU core count of a VM or LXC container.

    Requires confirm=true. At least one of memory_mb or cores must be provided.

    Behavior:
      - If the VM is stopped, change is immediate.
      - If the VM is running, Proxmox attempts hot-resize. This usually works
        for adding resources; reducing may need a reboot.
      - If hot-resize cannot apply, the new value is saved and takes effect
        on the next reboot. The task result indicates the situation.

    For QEMU VMs, RAM hotplug may require the VM to have memory hotplug
    enabled in its config (often it isn't by default); if so, a reboot
    is needed for the new RAM to be visible to the guest OS.
    """
    cfg = _require_config()
    if cfg:
        return cfg
    if not params.confirm:
        return _missing_confirm("proxmox_resize_vm")
    if params.memory_mb is None and params.cores is None:
        return "Error: Provide at least one of memory_mb or cores."

    payload: dict[str, Any] = {}
    if params.memory_mb is not None:
        payload["memory"] = params.memory_mb
    if params.cores is not None:
        payload["cores"] = params.cores

    path = f"/nodes/{params.node}/{params.vm_type}/{params.vmid}/config"

    try:
        async with _client() as c:
            r = await c.put(path, data=payload)
            r.raise_for_status()
            result = r.json().get("data")
    except Exception as exc:
        return _format_http_error(exc)

    changes = []
    if params.memory_mb is not None:
        changes.append(f"memory={params.memory_mb} MB")
    if params.cores is not None:
        changes.append(f"cores={params.cores}")

    msg = (
        f"OK: Config update applied to {params.vm_type} {params.vmid}: "
        f"{', '.join(changes)}."
    )
    if result:
        msg += f" Task: {result}."
    msg += (
        " If the VM was running and the guest OS does not reflect the change, "
        "a reboot may be required (use proxmox_vm_reboot)."
    )
    return msg


TOOLS = [
    "proxmox_list_nodes",
    "proxmox_get_node_status",
    "proxmox_list_vms",
    "proxmox_get_vm_status",
    "proxmox_list_storage",
    "proxmox_list_backups",
    "proxmox_list_snapshots",
    "proxmox_vm_start",
    "proxmox_vm_shutdown",
    "proxmox_vm_stop",
    "proxmox_vm_reboot",
    "proxmox_create_snapshot",
    "proxmox_rollback_snapshot",
    "proxmox_create_backup",
    "proxmox_resize_vm",
]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in {"-h", "--help"}:
        print(__doc__)
        print("Tools registered:")
        for t in TOOLS:
            print(f"  - {t}")
        sys.exit(0)
    mcp.run()
