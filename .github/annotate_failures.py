"""Turn a JUnit XML report's failures into annotations on the CI run page (readable without signing in)."""

import sys
import xml.etree.ElementTree as ET  # nosec B405: parses the report pytest just wrote on this runner

for case in ET.parse(sys.argv[1]).iter("testcase"):  # nosec B314
    for bad in [*case.iter("failure"), *case.iter("error")]:
        text = f"{bad.get('message') or ''}\n{(bad.text or '')[-1500:]}"
        print(f"::error title={case.get('name')}::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))
