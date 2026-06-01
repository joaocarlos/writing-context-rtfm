import shutil
from typing import Dict, Any

def autodiscover_local_mcps(project_root: str = ".") -> Dict[str, Dict[str, Any]]:
    """Scan the system PATH for installed Zotero MCP server executables.
    
    Returns:
        dict: mapping of provider_id to their server configuration dict.
    """
    discovered = {}

    # 1. PATH discovery for zotero-mcp
    zotero_path = shutil.which("zotero-mcp")
    if zotero_path:
        discovered["zotero"] = {
            "enabled": True,
            "mcp_server": {
                "command": "zotero-mcp",
                "args": []
            }
        }

    return discovered
