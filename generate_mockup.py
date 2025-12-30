
import base64
import math
import sys
from PIL import Image

def get_squircle_path(x, y, w, h, r, smoothing=4.8):
    """
    Generates an SVG path data string for a rectangle with continuous curvature corners (superellipse-like).
    """
    path_cmds = []
    
    def get_corner_points(cx, cy, start_angle_deg, end_angle_deg, steps=200):
        points = []
        for i in range(steps + 1):
            t = start_angle_deg + (end_angle_deg - start_angle_deg) * (i / steps)
            rad = math.radians(t)
            cos_val = math.cos(rad)
            sin_val = math.sin(rad)
            sx = 1 if cos_val >= 0 else -1
            sy = 1 if sin_val >= 0 else -1
            
            # Superellipse parametric
            px = cx + sx * r * (abs(cos_val) ** (2 / smoothing))
            py = cy + sy * r * (abs(sin_val) ** (2 / smoothing))
            points.append((px, py))
        return points

    # Top middle
    path_cmds.append(f"M {x + w/2} {y}")
    # Line to TR corner start
    path_cmds.append(f"L {x + w - r} {y}")
    
    # TR Corner
    cx, cy = x + w - r, y + r
    for px, py in get_corner_points(cx, cy, 270, 360):
        path_cmds.append(f"L {px:.2f} {py:.2f}")

    # Right Edge
    path_cmds.append(f"L {x + w} {y + h - r}")
    
    # BR Corner
    cx, cy = x + w - r, y + h - r
    for px, py in get_corner_points(cx, cy, 0, 90):
        path_cmds.append(f"L {px:.2f} {py:.2f}")

    # Bottom Edge
    path_cmds.append(f"L {x + r} {y + h}")
    
    # BL Corner
    cx, cy = x + r, y + h - r
    for px, py in get_corner_points(cx, cy, 90, 180):
        path_cmds.append(f"L {px:.2f} {py:.2f}")

    # Left Edge
    path_cmds.append(f"L {x} {y + r}")
    
    # TL Corner
    cx, cy = x + r, y + r
    for px, py in get_corner_points(cx, cy, 180, 270):
        path_cmds.append(f"L {px:.2f} {py:.2f}")
    
    path_cmds.append("Z")
    return " ".join(path_cmds)


def create_mockup(img_path="demo.jpg", output_path="demo_mockup.svg"):
    # 1. Read Image and Dimensions
    try:
        with Image.open(img_path) as img:
            img_w, img_h = img.size
    except Exception as e:
        print(f"Error reading image {img_path}: {e}")
        return

    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")
    
    print(f"Processing {img_path}: {img_w}x{img_h}")

    # 2. Configure Dimensions based on iPhone 16 Pro Max
    # Physical Reference:
    # Width: ~77.6mm -> We map this to `img_w` or a fixed width?
    # To keep image quality 1:1, we map the SCREEN WIDTH to img_w.
    
    # iPhone 16 Pro Max Screen Ratio
    # Real Resolution: ~2868 x 1320
    # Aspect Ratio: 2.1727
    
    # However, sometimes screenshots are already resized or from different devices.
    # We should enforce the 'Phone Frame' aspect ratio to look like a phone.
    
    screen_w = img_w
    screen_h = int(screen_w * 2.1727) # Ideal screen height for this width
    
    # Check if image is a "Long Screenshot"
    is_long_screenshot = img_h > (screen_h * 1.1) # Buffer
    
    # Bezel Calculation (1.15mm relative to 75.3mm screen width)
    # Ratio: 1.15 / 75.3 ~= 0.01527
    bezel = int(screen_w * 0.0153)
    if bezel < 4: bezel = 4 # Min bezel
    
    # Body Corner Radius
    # Pro Max is very rounded. Ratio ~18mm / 77.6mm ~= 0.23
    body_radius = int((screen_w + 2*bezel) * 0.23)
    screen_radius = body_radius - bezel
    
    # Dynamic Island
    # Width ~31% of screen
    island_w = int(screen_w * 0.31)
    island_h = int(island_w * 0.29) # Ratio check
    island_top_margin = int(screen_w * 0.04) # ~4% from top edge of screen?
    # Usually strictly positioned.
    # Let's use a safe ratio.
    
    frames_count = 2 if is_long_screenshot else 1
    
    # Frame Dimensions
    frame_w = screen_w + (bezel * 2)
    frame_h = screen_h + (bezel * 2)
    
    spacer = int(frame_w * 0.1) # 10% spacer
    
    total_w = (frame_w * frames_count) + (spacer * (frames_count - 1))
    total_h = frame_h
    
    # Paths
    body_path = get_squircle_path(0, 0, frame_w, frame_h, body_radius, smoothing=5.0)
    screen_path = get_squircle_path(bezel, bezel, screen_w, screen_h, screen_radius, smoothing=5.0)
    
    svg_defs = f"""
    <defs>
        <rect id="dynamic_island" x="{bezel + (screen_w - island_w)/2}" y="{bezel + island_top_margin}" width="{island_w}" height="{island_h}" rx="{island_h/2}" ry="{island_h/2}" fill="black" />
        
        <path id="phone_body_s" d="{body_path}" />
        <path id="screen_s" d="{screen_path}" />
        
        <image id="source_img" width="{img_w}" height="{img_h}" xlink:href="data:image/jpeg;base64,{img_b64}" />
    </defs>
    """
    
    def generate_phone_group(offset_x, alignment):
        # alignment: 'top' or 'bottom' or 'center' (if single)
        
        img_y = bezel # Default top align
        if alignment == 'bottom':
            img_y = bezel + screen_h - img_h
        elif alignment == 'center':
             img_y = bezel + (screen_h - img_h)/2
             
        return f"""
    <g transform="translate({offset_x}, 0)">
        <!-- Body -->
        <use href="#phone_body_s" fill="#222" />
        <use href="#phone_body_s" fill="none" stroke="#555" stroke-width="{max(2, frame_w*0.005)}" stroke-linejoin="round" stroke-linecap="round" />
        <use href="#phone_body_s" fill="none" stroke="#111" stroke-width="{max(1, frame_w*0.002)}" transform="translate({frame_w*0.002},{frame_w*0.002}) scale(0.998)" stroke-linejoin="round" />
        
        <!-- Screen Content -->
        <g>
            <clipPath id="clip_{alignment}_{offset_x}"><use href="#screen_s" /></clipPath>
            <g clip-path="url(#clip_{alignment}_{offset_x})">
                <rect x="{bezel}" y="{bezel}" width="{screen_w}" height="{screen_h}" fill="white" />
                <use href="#source_img" x="{bezel}" y="{img_y}" />
                
                <!-- Inner Shadow -->
                <use href="#screen_s" fill="none" stroke="black" stroke-width="{max(4, frame_w*0.01)}" opacity="0.1" stroke-linejoin="round" />
            </g>
        </g>
        
        <use href="#dynamic_island" />
    </g>
        """

    svg_body = ""
    
    if frames_count == 2:
        # Left: Top
        svg_body += generate_phone_group(0, 'top')
        # Right: Bottom
        svg_body += generate_phone_group(frame_w + spacer, 'bottom')
    else:
        # Single: Top or Fit? 
        # If image is smaller than screen, center it?
        # Usually standard screenshot is Top aligned or Full fit.
        # We'll assume Top or 'fit' if exactly same ratio.
        svg_body += generate_phone_group(0, 'top')

    svg_content = f"""<svg width="{total_w}" height="{total_h}" viewBox="0 0 {total_w} {total_h}" fill="none" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
    {svg_defs}
    {svg_body}
</svg>
"""
    with open(output_path, "w") as f:
        f.write(svg_content)
    print(f"Generated {output_path}")

if __name__ == "__main__":
    create_mockup()
