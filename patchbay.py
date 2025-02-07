#!/usr/bin/env python3
"""
Unified ALSA and JACK (or PipeWire-JACK) patchbay with color-coded port display,
active connection listing, and scrolling support for panels.

Key bindings:
  - TAB:      Cycle focus among "input" (left column), "output" (right column),
              and "active" (active connections panel).
  - UP/DOWN:  Navigate in the focused panel.
  - c:        Connect the selected input port (left) to the selected output port (right)
              (only if both ports are of the same type).
  - d:        Disconnect the connection:
                • If focus is on the active connections panel, disconnect that connection.
                • Otherwise, disconnect the connection between the selected ports.
  - r:        Refresh the views.
  - q:        Quit.

Ports are drawn with different background colors:
  - ALSA ports: red background.
  - JACK ports: blue background.

The bottom section shows only active connections.
If a panel has more items than can be displayed, it will scroll.
The default size of the bottom panel has been increased to 8 lines.
"""

import curses
import subprocess
import re

# -------------------------------
# Helper classes and functions
# -------------------------------

# Data structure for ALSA ports.
class APort:
    def __init__(self, client, port, name):
        self.client = client
        self.port = port
        self.name = name

    def id(self):
        """Return the port identifier (format: client:port)."""
        return f"{self.client}:{self.port}"

    def display(self):
        """Return a display string combining the ID and the port name."""
        return f"{self.id()}  {self.name}"

def parse_aconnect_output(output):
    """Parse output from 'aconnect -i' or 'aconnect -o' into a list of APort objects."""
    ports = []
    current_client = None
    for line in output.splitlines():
        line = line.rstrip()
        if line.startswith("client"):
            m = re.match(r"client\s+(\d+):\s+'([^']+)'", line)
            if m:
                current_client = m.group(1)
        elif re.match(r"^\s*\d+", line):
            m = re.match(r"\s*(\d+)\s+'([^']+)'", line)
            if m and current_client is not None:
                port_num = m.group(1)
                port_name = m.group(2)
                ports.append(APort(current_client, port_num, port_name))
    return ports

def get_alsa_ports(direction):
    """Return a list of APort objects for ALSA; direction should be '-i' or '-o'."""
    try:
        output = subprocess.check_output(["aconnect", direction]).decode("utf-8", errors="ignore")
        return parse_aconnect_output(output)
    except Exception:
        return []

def get_jack_ports():
    """
    Return a tuple (jack_outputs, jack_inputs) using jack_lsp.
    Standard error is suppressed.
    """
    try:
        out = subprocess.check_output(["jack_lsp"], stderr=subprocess.DEVNULL)\
                       .decode("utf-8", errors="ignore")
        port_list = [line.strip() for line in out.splitlines() if line.strip()]
        outputs = []
        inputs = []
        for port in port_list:
            lower = port.lower()
            if "capture" in lower or "input" in lower:
                inputs.append(port)
            elif "playback" in lower or "output" in lower:
                outputs.append(port)
            else:
                outputs.append(port)
                inputs.append(port)
        def unique(seq):
            seen = set()
            res = []
            for item in seq:
                if item not in seen:
                    res.append(item)
                    seen.add(item)
            return res
        return unique(outputs), unique(inputs)
    except Exception:
        return [], []

def get_all_input_ports():
    """
    Combine ALSA and JACK input ports into a single list.
    Each entry is a tuple: (display_string, port_type, connection_id)
    """
    ports = []
    for p in get_alsa_ports("-i"):
        ports.append((p.display(), "ALSA", p.id()))
    jack_outputs, jack_inputs = get_jack_ports()
    for port in jack_inputs:
        ports.append((port, "JACK", port))
    return ports

def get_all_output_ports():
    """
    Combine ALSA and JACK output ports into a single list.
    Each entry is a tuple: (display_string, port_type, connection_id)
    """
    ports = []
    for p in get_alsa_ports("-o"):
        ports.append((p.display(), "ALSA", p.id()))
    jack_outputs, jack_inputs = get_jack_ports()
    for port in jack_outputs:
        ports.append((port, "JACK", port))
    return ports

def get_active_connections_alsa():
    """
    Parse "aconnect -l" to obtain active ALSA connections.
    Returns a list of connection strings like "16:0 -> 128:0".
    """
    try:
        output = subprocess.check_output(["aconnect", "-l"]).decode("utf-8", errors="ignore")
        lines = output.splitlines()
        connections = []
        current_client = None
        current_port = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("client"):
                m = re.match(r"client\s+(\d+):", stripped)
                if m:
                    current_client = m.group(1)
                current_port = None
            elif re.match(r"^\d+\s+'", stripped):
                m = re.match(r"^(\d+)\s+'", stripped)
                if m:
                    current_port = m.group(1)
            elif "Connecting To:" in line:
                m = re.search(r"Connecting To:\s*(\d+:\d+)", line)
                if m and current_client is not None and current_port is not None:
                    src = f"{current_client}:{current_port}"
                    dst = m.group(1)
                    conn = f"{src} -> {dst}"
                    if conn not in connections:
                        connections.append(conn)
            elif "Connected From:" in line:
                m = re.search(r"Connected From:\s*(\d+:\d+)", line)
                if m and current_client is not None and current_port is not None:
                    dst = f"{current_client}:{current_port}"
                    src = m.group(1)
                    conn = f"{src} -> {dst}"
                    if conn not in connections:
                        connections.append(conn)
        return connections
    except Exception:
        return []

def get_active_connections_jack():
    """
    Return a list of active JACK connections by parsing the output of "jack_lsp --connections".
    The output is assumed to be grouped: an unindented line is a source port,
    and subsequent indented lines are destination ports.
    Each connection is returned as "source -> destination".
    """
    try:
        output = subprocess.check_output(["jack_lsp", "--connections"], stderr=subprocess.DEVNULL)\
                        .decode("utf-8", errors="ignore")
        lines = output.splitlines()
        connections = []
        current_source = None
        for line in lines:
            if line and not line.startswith(" "):
                current_source = line.strip()
            elif line.startswith(" ") and current_source is not None:
                dest = line.strip()
                if dest:
                    connections.append(f"{current_source} -> {dest}")
        return connections
    except Exception:
        return []

def get_all_active_connections():
    """Combine active connections from ALSA and JACK."""
    return get_active_connections_alsa() + get_active_connections_jack()

def determine_connection_color(conn_line):
    """
    Return a curses color pair for the connection string.
    If the source (before "->") is in ALSA style (digits:digits), return red;
    otherwise, return blue.
    """
    parts = conn_line.split("->")
    if len(parts) < 2:
        return curses.A_NORMAL
    src = parts[0].strip()
    if re.match(r"^\d+:\d+$", src):
        return curses.color_pair(1)
    else:
        return curses.color_pair(2)

def alsa_connect(src, dst):
    """Connect two ALSA ports using aconnect."""
    try:
        subprocess.check_call(["aconnect", src, dst])
        return True, "Connected (ALSA)."
    except subprocess.CalledProcessError as e:
        return False, f"ALSA connect error: {e}"

def alsa_disconnect(src, dst):
    """Disconnect two ALSA ports using aconnect."""
    try:
        subprocess.check_call(["aconnect", "-d", src, dst])
        return True, "Disconnected (ALSA)."
    except subprocess.CalledProcessError as e:
        return False, f"ALSA disconnect error: {e}"

def jack_connect(src, dst):
    """Connect two JACK ports using jack_connect (stderr suppressed)."""
    try:
        subprocess.check_call(["jack_connect", src, dst], stderr=subprocess.DEVNULL)
        return True, "Connected (JACK)."
    except subprocess.CalledProcessError as e:
        return False, f"JACK connect error: {e}"

def jack_disconnect(src, dst):
    """Disconnect two JACK ports using jack_disconnect (stderr suppressed)."""
    try:
        subprocess.check_call(["jack_disconnect", src, dst], stderr=subprocess.DEVNULL)
        return True, "Disconnected (JACK)."
    except subprocess.CalledProcessError as e:
        return False, f"JACK disconnect error: {e}"

def disconnect_connection(conn_line):
    """
    Given a connection string "src -> dst", choose ALSA or JACK based on the source format.
    """
    parts = conn_line.split("->")
    if len(parts) != 2:
        return False, "Invalid connection format."
    src = parts[0].strip()
    dst = parts[1].strip()
    if re.match(r"^\d+:\d+$", src):
        return alsa_disconnect(src, dst)
    else:
        return jack_disconnect(src, dst)

# -------------------------------
# Main curses UI loop (using stdscr directly)
# -------------------------------
def main(stdscr):
    # Initialize curses settings.
    curses.curs_set(0)
    stdscr.keypad(True)
    curses.noecho()
    curses.cbreak()
    curses.start_color()
    # Define two color pairs.
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_RED)    # ALSA: white on red.
    curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLUE)   # JACK: white on blue.

    status = "Welcome to the unified patchbay!"
    focus = "input"   # can be "input", "output", or "active"
    sel_input = 0
    sel_output = 0
    sel_active = 0

    # Initialize offsets for scrolling in each panel.
    offset_input = 0
    offset_output = 0
    offset_active = 0

    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        # Increase the default bottom panel height to 8 lines.
        bottom_panel_height = 8 if height >= 16 else max(3, height // 3)
        patchbay_height = height - bottom_panel_height - 1

        # --- Draw Global Header (row 0) ---
        header_text = f"Focus: {focus} | TAB: switch focus | c: connect | d: disconnect | r: refresh | q: quit"
        stdscr.addstr(0, 0, header_text[:width])

        # --- Draw Patchbay (top panel) ---
        stdscr.addstr(1, 2, "Input Ports")
        stdscr.addstr(1, width//2 + 2, "Output Ports")
        input_ports = get_all_input_ports()
        output_ports = get_all_output_ports()
        patchbay_list_rows = patchbay_height - 2

        # Update scrolling offsets for input ports.
        if sel_input < offset_input:
            offset_input = sel_input
        elif sel_input >= offset_input + patchbay_list_rows:
            offset_input = sel_input - patchbay_list_rows + 1

        # Draw visible input ports.
        for idx in range(offset_input, min(offset_input + patchbay_list_rows, len(input_ports))):
            y = 2 + (idx - offset_input)
            x = 2
            display_text, p_type, _ = input_ports[idx]
            base_attr = curses.color_pair(1) if p_type == "ALSA" else curses.color_pair(2)
            if idx == sel_input:
                attr = base_attr | (curses.A_REVERSE if focus == "input" else curses.A_UNDERLINE)
            else:
                attr = base_attr
            stdscr.addstr(y, x, display_text[:(width//2 - 4)], attr)

        # Update scrolling offsets for output ports.
        if sel_output < offset_output:
            offset_output = sel_output
        elif sel_output >= offset_output + patchbay_list_rows:
            offset_output = sel_output - patchbay_list_rows + 1

        # Draw visible output ports.
        for idx in range(offset_output, min(offset_output + patchbay_list_rows, len(output_ports))):
            y = 2 + (idx - offset_output)
            x = width//2 + 2
            display_text, p_type, _ = output_ports[idx]
            base_attr = curses.color_pair(1) if p_type == "ALSA" else curses.color_pair(2)
            if idx == sel_output:
                attr = base_attr | (curses.A_REVERSE if focus == "output" else curses.A_UNDERLINE)
            else:
                attr = base_attr
            stdscr.addstr(y, x, display_text[:(width//2 - 4)], attr)

        # --- Draw Horizontal Separator ---
        stdscr.hline(patchbay_height, 0, curses.ACS_HLINE, width)

        # --- Draw Active Connections (bottom panel) ---
        stdscr.addstr(patchbay_height + 1, 2, "Active Connections")
        active_conns = get_all_active_connections()
        active_list_rows = height - (patchbay_height + 2) - 1

        # Update scrolling offsets for active connections.
        if sel_active < offset_active:
            offset_active = sel_active
        elif sel_active >= offset_active + active_list_rows:
            offset_active = sel_active - active_list_rows + 1

        if not active_conns:
            stdscr.addstr(patchbay_height + 2, 2, "No active connections.")
        else:
            for idx in range(offset_active, min(offset_active + active_list_rows, len(active_conns))):
                y = patchbay_height + 2 + (idx - offset_active)
                x = 2
                conn_text = active_conns[idx][: (width - 4)]
                base_attr = determine_connection_color(active_conns[idx])
                if idx == sel_active:
                    attr = base_attr | (curses.A_REVERSE if focus == "active" else curses.A_UNDERLINE)
                else:
                    attr = base_attr
                stdscr.addstr(y, x, conn_text, attr)

        # --- Draw Status Bar (last row) ---
        stdscr.addstr(height - 1, 0, status[:width])
        stdscr.refresh()

        # --- Handle User Input ---
        key = stdscr.getch()
        if key == ord('q'):
            break
        elif key == ord('r'):
            status = "Views refreshed."
        elif key == 9:  # TAB key: cycle focus.
            if focus == "input":
                focus = "output"
            elif focus == "output":
                focus = "active"
            else:
                focus = "input"
        elif key == curses.KEY_UP:
            if focus == "input" and sel_input > 0:
                sel_input -= 1
            elif focus == "output" and sel_output > 0:
                sel_output -= 1
            elif focus == "active" and sel_active > 0:
                sel_active -= 1
        elif key == curses.KEY_DOWN:
            if focus == "input" and sel_input < len(input_ports) - 1:
                sel_input += 1
            elif focus == "output" and sel_output < len(output_ports) - 1:
                sel_output += 1
            elif focus == "active" and sel_active < len(active_conns) - 1:
                sel_active += 1
        elif key == ord('c'):
            if focus in ("input", "output"):
                if not input_ports or not output_ports:
                    status = "No ports available for connection."
                else:
                    inp = input_ports[sel_input]   # (display, type, id)
                    outp = output_ports[sel_output]
                    if inp[1] != outp[1]:
                        status = "Cannot connect ports of different types."
                    else:
                        if inp[1] == "ALSA":
                            ok, msg = alsa_connect(inp[2], outp[2])
                        else:
                            ok, msg = jack_connect(inp[2], outp[2])
                        status = msg
            else:
                status = "Cannot connect when active connections panel is focused."
        elif key == ord('d'):
            if focus == "active":
                if active_conns:
                    conn_line = active_conns[sel_active]
                    ok, msg = disconnect_connection(conn_line)
                    status = msg
                    if sel_active >= len(active_conns) - 1:
                        sel_active = max(0, len(active_conns) - 2)
                else:
                    status = "No active connections to disconnect."
            else:
                if not input_ports or not output_ports:
                    status = "No ports available for disconnection."
                else:
                    inp = input_ports[sel_input]
                    outp = output_ports[sel_output]
                    if inp[1] != outp[1]:
                        status = "Cannot disconnect ports of different types."
                    else:
                        if inp[1] == "ALSA":
                            ok, msg = alsa_disconnect(inp[2], outp[2])
                        else:
                            ok, msg = jack_disconnect(inp[2], outp[2])
                        status = msg
        else:
            status = f"Key {key} pressed."

if __name__ == '__main__':
    curses.wrapper(main)
