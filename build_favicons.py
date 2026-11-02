import asyncio
import os
import struct
from playwright.async_api import async_playwright

STATIC_DIR = "C:/YAPF_Farm/static"
SVG_FILE = os.path.join(STATIC_DIR, "favicon.svg").replace("\\", "/")

def create_ico(images_data, output_path):
    """
    images_data: list of tuples (width, height, png_bytes)
    Writes a valid multi-resolution Windows ICO file containing PNG streams.
    """
    num_images = len(images_data)
    header = struct.pack("<HHH", 0, 1, num_images)
    
    entries = []
    offset = 6 + (num_images * 16)
    
    for width, height, data in images_data:
        w_byte = 0 if width >= 256 else width
        h_byte = 0 if height >= 256 else height
        size = len(data)
        entry = struct.pack("<BBBBHHII", w_byte, h_byte, 0, 0, 1, 32, size, offset)
        entries.append(entry)
        offset += size
        
    with open(output_path, "wb") as f:
        f.write(header)
        for e in entries:
            f.write(e)
        for _, _, data in images_data:
            f.write(data)

async def generate_favicons():
    os.makedirs(STATIC_DIR, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        sizes = [
            (512, "favicon-512x512.png"),
            (180, "apple-touch-icon.png"),
            (48, "favicon-48x48.png"),
            (32, "favicon-32x32.png"),
            (16, "favicon-16x16.png")
        ]
        
        ico_parts = []
        
        for size, filename in sizes:
            out_file = os.path.join(STATIC_DIR, filename).replace("\\", "/")
            page = await browser.new_page(viewport={"width": size, "height": size})
            await page.goto(f"file:///{SVG_FILE}")
            await page.screenshot(path=out_file, omit_background=True)
            print(f"[FAVICON] Generated {filename} ({size}x{size})")
            
            if size in [16, 32, 48]:
                with open(out_file, "rb") as f:
                    ico_parts.append((size, size, f.read()))
            await page.close()
            
        ico_file = os.path.join(STATIC_DIR, "favicon.ico")
        create_ico(ico_parts, ico_file)
        print(f"[FAVICON] Generated multi-resolution favicon.ico ({os.path.getsize(ico_file)} bytes)")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(generate_favicons())
