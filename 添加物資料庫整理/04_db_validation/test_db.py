import urllib.request
import ssl
import re

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

url = "https://www.inchem.org/"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})

try:
    with urllib.request.urlopen(req, context=ctx, timeout=15) as response:
        body = response.read().decode('utf-8', 'ignore')
        
        # Find all script src
        print("=== External Script Src ===")
        srcs = re.findall(r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']', body)
        for s in srcs:
            print(s)
            
except Exception as e:
    print(f"Error: {e}")
