"""Robust rsync check - download last 5KB of log directly."""
import asyncio
import asyncssh
import sys

async def main():
    out = []
    try:
        async with asyncssh.connect(
            host='192.168.1.107', port=22, username='root',
            client_keys=[r'C:\Users\ahmet\.ssh\proxmox_ed25749'.replace('vm102_key', 'proxmox_ed25519').replace('4974', '4974')],  # workaround
            known_hosts=None, connect_timeout=15) as conn:
            out.append('CONNECTED')
    except Exception as e:
        out.append(f'CONN FAIL: {e}')
    with open(r'C:\files\proxmox-mcp\_check_diag.txt', 'w') as f:
        f.write('\n'.join(out))

asyncio.run(main())
