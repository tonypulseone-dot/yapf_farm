import asyncio
import random
import math

def cubic_bezier(p0, p1, p2, p3, t):
    """Calculate point on cubic bezier curve at parameter t (0 to 1)."""
    return (
        (1 - t)**3 * p0 +
        3 * (1 - t)**2 * t * p1 +
        3 * (1 - t) * t**2 * p2 +
        t**3 * p3
    )

async def human_mouse_move(page, start_x, start_y, end_x, end_y, steps=25):
    """Move mouse along a realistic curved path with jitter and variable velocity."""
    # Control points with natural deviation
    dx = end_x - start_x
    dy = end_y - start_y
    dist = math.hypot(dx, dy)
    
    if dist < 5:
        await page.mouse.move(end_x, end_y)
        return

    ctrl_dev = dist * 0.25
    cp1_x = start_x + dx * 0.3 + random.uniform(-ctrl_dev, ctrl_dev)
    cp1_y = start_y + dy * 0.1 + random.uniform(-ctrl_dev, ctrl_dev)
    cp2_x = start_x + dx * 0.7 + random.uniform(-ctrl_dev, ctrl_dev)
    cp2_y = start_y + dy * 0.9 + random.uniform(-ctrl_dev, ctrl_dev)

    for i in range(1, steps + 1):
        t = i / steps
        # Non-linear easing (slow start, fast middle, slow end)
        t_eased = math.sin(t * math.pi / 2) if t < 0.5 else 1 - math.cos(t * math.pi / 2)
        
        curr_x = cubic_bezier(start_x, cp1_x, cp2_x, end_x, t) + random.uniform(-1, 1)
        curr_y = cubic_bezier(start_y, cp1_y, cp2_y, end_y, t) + random.uniform(-1, 1)
        
        await page.mouse.move(curr_x, curr_y)
        await asyncio.sleep(random.uniform(0.008, 0.025))
        
    await page.mouse.move(end_x, end_y)

async def human_scroll(page, total_pixels=400, steps=8):
    """Simulate human wheel scroll with micro-pauses."""
    direction = 1 if total_pixels > 0 else -1
    remaining = abs(total_pixels)
    
    while remaining > 0:
        chunk = min(remaining, random.randint(40, 120)) * direction
        await page.mouse.wheel(0, chunk)
        remaining -= abs(chunk)
        await asyncio.sleep(random.uniform(0.05, 0.25))
    
    # Pause to 'read'
    await asyncio.sleep(random.uniform(0.5, 2.0))

async def human_type(page, selector, text):
    """Type text character-by-character with natural typing rhythms and rare corrected typos."""
    element = await page.wait_for_selector(selector, state="visible", timeout=10000)
    await element.click()
    await asyncio.sleep(random.uniform(0.2, 0.5))
    
    for char in text:
        # 1.5% chance to make a realistic typo and correct it
        if random.random() < 0.015 and char.isalpha():
            typo = random.choice("фывапролджйцукенгшщзхъ")
            await page.keyboard.type(typo)
            await asyncio.sleep(random.uniform(0.12, 0.28))
            await page.keyboard.press("Backspace")
            await asyncio.sleep(random.uniform(0.1, 0.25))

        await page.keyboard.type(char)
        # Variable inter-key delays (50ms - 190ms)
        await asyncio.sleep(random.uniform(0.05, 0.19))
    
    await asyncio.sleep(random.uniform(0.4, 0.9))
