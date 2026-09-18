# VS Code over SSH - see/edit the remote VPS filesystem from your PC

Two options. Option A (recommended) = full VS Code editor window attached to the remote.
Option B = lightweight "just browse the files" without installing anything.

===============================================================================
OPTION A - VS Code Remote-SSH (full editor, the standard way)
===============================================================================
What it gives you: a normal VS Code window that feels local, but is actually
running on the VPS. You can browse, edit, search, open terminals - all on the
remote. Uses your existing ssh config (so `vps` works automatically).

STEPS
1. Install VS Code on your PC (if not already):
   https://code.visualstudio.com

2. Install the "Remote - SSH" extension:
   - In VS Code: click the Extensions icon (or Ctrl+Shift+X)
   - Search:  Remote - SSH
   - Install (it's by Microsoft)

3. Open the remote:
   - Press  Ctrl+Shift+P   (Command Palette)
   - Type:  Remote-SSH: Connect to Host...
   - Pick:  vps        (it reads your ~/.ssh/config, so `vps` shows up)
   - First time it installs a small server on the VPS, then opens a window

4. Open the folder you want:
   - In the new remote window:  File > Open Folder
   - Type the remote path, e.g.  /opt/ses   or  /opt/ses/.hermes-stage
   - You'll now see the full file tree in the Explorer on the left.

5. (Bonus) If you also open a terminal in VS Code (Terminal > New Terminal),
   it opens ON THE VPS, so you have a shell + files in one place.

WHY THIS IS GOOD FOR YOU
- You can visually confirm the file structure your VPS agent created.
- You can read .env key NAMES, config.yaml, skills/, cron/ etc.
- No secrets are copied to your PC - you're just viewing the remote.

===============================================================================
OPTION B - "just see the file tree" without VS Code (zero install)
===============================================================================
If you only need to LOOK at the structure (not edit), the VPS terminal already
lets you list files. From your PC:
    ssh vps "find /opt/ses -maxdepth 2 -type d | sort"
    ssh vps "ls -la /opt/ses/.hermes-stage"
    ssh vps "tree /opt/ses 2>/dev/null || find /opt/ses -maxdepth 2 | sort"

That prints the directory tree to your terminal. Good for a quick look; not an
editor.

===============================================================================
WHY NOT SFTP/other tools for you
===============================================================================
SFTP clients (WinSCP, FileZilla) also work (Host: vps, or the IP, user root,
port 22) but they're extra apps to learn and they copy files around - they don't
give you the "looks like local editing" feel. VS Code Remote-SSH is one app you
likely already have, integrates with your ~/.ssh/config, and is what most people
use for exactly this.

===============================================================================
THE SAFETY NOTE THAT MATTERS
===============================================================================
You can SEE and EDIT files on the VPS. That's fine. But be careful editing the
live ops.db or the container's .env values - you can look, and if you change a
SECRET you may break the Hermes instance. View-first, edit-only-what-you-understand.
