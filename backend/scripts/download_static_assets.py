"""
Script baraye download-e static assets (JS/CSS/Fonts) az CDN
Baraye karbord dar halat offline pas az download-e avval
"""

import os
import urllib.request
import ssl
from pathlib import Path

# Tarif asset-ha va URL-haye CDN
ASSETS = {
    "js/tailwind.min.js": "https://cdn.tailwindcss.com/3.4.17",
    "js/alpine.min.js": "https://cdn.jsdelivr.net/npm/alpinejs@3.14.3/dist/cdn.min.js",
    "fonts/inter.css": "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap",
    "fonts/Inter-Regular.ttf": "https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip",  # Alternative
}

# Font files baraye Inter (direct links)
INTER_FONT_FILES = {
    "Inter-Regular.woff2": "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTcviYwY9GWFzK7AbCrZeiN_Ar_LAgzwmDn5Oz4mOrQ.woff2",
    "Inter-Medium.woff2": "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTcviYwY9GWFzK7AbCrZeiN_Ar_LAgzwmDn5Oz4mOrQ.woff2",
    "Inter-SemiBold.woff2": "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTcviYwY9GWFzK7AbCrZeiN_Ar_LAgzwmDn5Oz4mOrQ.woff2",
    "Inter-Bold.woff2": "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTcviYwY9GWFzK7AbCrZeiN_Ar_LAgzwmDn5Oz4mOrQ.woff2",
}


def get_static_dir():
    """Gereftan masire static directory"""
    script_dir = Path(__file__).parent.parent  # backend/
    static_dir = script_dir / "static"
    return static_dir


def ensure_directories(static_dir):
    """Sakhtan subdirectories agar vojud nadashand"""
    for subdir in ["js", "fonts", "css", "img/auth"]:
        (static_dir / subdir).mkdir(parents=True, exist_ok=True)


def download_file(url: str, dest_path: Path, timeout: int = 30) -> bool:
    """Download yek file az URL be destination"""
    try:
        # SSL context baraye dorost kar kardan ba HTTPS
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        req = urllib.request.Request(url, headers=headers)
        
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as response:
            content = response.read()
            dest_path.write_bytes(content)
            print(f"✓ Download shod: {dest_path.name} ({len(content)} bytes)")
            return True
            
    except Exception as e:
        print(f"✗ Error dar download {url}: {e}")
        return False


def create_local_inter_css(static_dir: Path) -> bool:
    """Sakhtan file CSS baraye font-e local Inter"""
    css_content = '''/* Inter Font - Local CSS */
@font-face {
    font-family: 'Inter';
    src: url('Inter-Regular.woff2') format('woff2');
    font-weight: 300 400;
    font-style: normal;
    font-display: swap;
}

@font-face {
    font-family: 'Inter';
    src: url('Inter-Medium.woff2') format('woff2');
    font-weight: 500;
    font-style: normal;
    font-display: swap;
}

@font-face {
    font-family: 'Inter';
    src: url('Inter-SemiBold.woff2') format('woff2');
    font-weight: 600;
    font-style: normal;
    font-display: swap;
}

@font-face {
    font-family: 'Inter';
    src: url('Inter-Bold.woff2') format('woff2');
    font-weight: 700;
    font-style: normal;
    font-display: swap;
}
'''
    try:
        css_path = static_dir / "fonts" / "inter.css"
        css_path.write_text(css_content, encoding='utf-8')
        print("✓ Sakht shod: inter.css (local font definitions)")
        return True
    except Exception as e:
        print(f"✗ Error dar sakht inter.css: {e}")
        return False


def download_all_assets(force: bool = False) -> dict:
    """
Download hameye asset-ha agar vojud nadarand
    
    Args:
        force: Agar True, hame ra dobare download kon hatta agar vojud dashte bashand
    
    Returns:
        dict: Status-e har file {filename: success_bool}
    """
    static_dir = get_static_dir()
    ensure_directories(static_dir)
    
    results = {}
    
    print("=" * 50)
    print("Download Static Assets - CAMEO Project")
    print("=" * 50)
    print(f"Static directory: {static_dir}")
    print()
    
    # 1. Download Tailwind CSS
    tailwind_path = static_dir / "js" / "tailwind.min.js"
    if not tailwind_path.exists() or force:
        print("⬇ Download Tailwind CSS v3.4.17...")
        results["tailwind.min.js"] = download_file(
            "https://cdn.tailwindcss.com/3.4.17",
            tailwind_path
        )
    else:
        print(f"✓ Tailwind CSS ghablan download shode ast ({tailwind_path.stat().st_size} bytes)")
        results["tailwind.min.js"] = True
    
    # 2. Download Alpine.js
    alpine_path = static_dir / "js" / "alpine.min.js"
    if not alpine_path.exists() or force:
        print("⬇ Download Alpine.js v3.14.3...")
        results["alpine.min.js"] = download_file(
            "https://cdn.jsdelivr.net/npm/alpinejs@3.14.3/dist/cdn.min.js",
            alpine_path
        )
    else:
        print(f"✓ Alpine.js ghablan download shode ast ({alpine_path.stat().st_size} bytes)")
        results["alpine.min.js"] = True
    
    # 3. Download Inter font files va sakhtan CSS
    print()
    print("⬇ Check kardane font-haye Inter...")
    
    fonts_downloaded = 0
    for font_name, font_url in INTER_FONT_FILES.items():
        font_path = static_dir / "fonts" / font_name
        if not font_path.exists() or force:
            if download_file(font_url, font_path):
                fonts_downloaded += 1
        else:
            fonts_downloaded += 1
    
    # Sakhtan inter.css baraye estefade az font-haye local
    css_path = static_dir / "fonts" / "inter.css"
    if not css_path.exists() or force:
        results["inter.css"] = create_local_inter_css(static_dir)
    else:
        results["inter.css"] = True
    
    # Summary
    print()
    print("=" * 50)
    print("Summary:")
    success_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    print(f"✓ {success_count}/{total_count} file download shod/sakht shod")
    
    if all(results.values()):
        print("✓ Hameye asset-ha amade estefade hastand!")
    else:
        print("⚠ Ba'zi file-ha download nashodand. Server online mikhahad.")
    
    print("=" * 50)
    
    return results


def check_assets_exist() -> bool:
    """Check kardane inke hameye asset-ha vojud darand ya na"""
    static_dir = get_static_dir()
    
    required_files = [
        static_dir / "js" / "tailwind.min.js",
        static_dir / "js" / "alpine.min.js",
        static_dir / "fonts" / "inter.css",
    ]
    
    return all(f.exists() for f in required_files)


if __name__ == "__main__":
    # Run shodan az command line
    import sys
    force = "--force" in sys.argv or "-f" in sys.argv
    
    results = download_all_assets(force=force)
    
    # Exit code baraye CI/CD
    if all(results.values()):
        exit(0)
    else:
        exit(1)
