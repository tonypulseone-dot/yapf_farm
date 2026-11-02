import aiohttp
import asyncio
import logging

logger = logging.getLogger("proxy")

def parse_proxy_line(line, default_scheme="http"):
    """
    Parse proxy formats:
    - IP:PORT:USER:PASS
    - IP:PORT:USER:PASS#https://rotate.link/rotate?key=123
    - IP:PORT:USER:PASS|https://rotate.link/rotate?key=123
    - http://user:pass@ip:port
    - socks5://user:pass@ip:port
    """
    if not line or not line.strip():
        return None, None
        
    line = line.strip()
    rotate_url = None
    
    # Check for rotation URL delimiter (# or |)
    if "#" in line:
        parts = line.split("#", 1)
        line = parts[0].strip()
        rotate_url = parts[1].strip() or None
    elif "|" in line:
        parts = line.split("|", 1)
        line = parts[0].strip()
        rotate_url = parts[1].strip() or None

    scheme = (default_scheme or "http").lower()
    if "://" in line:
        parts = line.split("://", 1)
        scheme = parts[0].lower()
        line = parts[1]
        
    # Check format user:pass@ip:port
    if "@" in line:
        auth, host_port = line.split("@", 1)
        user, pwd = auth.split(":", 1)
        ip, port = host_port.split(":", 1)
    else:
        parts = line.split(":")
        if len(parts) == 4:
            ip, port, user, pwd = parts
        elif len(parts) == 2:
            ip, port = parts
            user, pwd = None, None
        else:
            return None, None

    playwright_proxy = {
        "server": f"{scheme}://{ip}:{port}"
    }
    if user and pwd:
        playwright_proxy["username"] = user
        playwright_proxy["password"] = pwd
        
    return playwright_proxy, rotate_url

async def trigger_proxy_rotation(rotate_url):
    """Call rotation link to change IP on mobile proxy. Safe no-op if no URL."""
    if not rotate_url or not str(rotate_url).strip():
        return True
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(rotate_url.strip(), timeout=15) as resp:
                logger.info(f"Rotation webhook triggered: status={resp.status}")
                await asyncio.sleep(3)  # Wait for reconnection
                return True
    except Exception as e:
        logger.warning(f"Error rotating proxy IP: {e}")
        return False
