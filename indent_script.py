import sys

def modify_bot_engine():
    with open('bot_engine.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()

    start_idx = -1
    end_idx = -1
    for i, line in enumerate(lines):
        if 'total_posted = 0' in line and i > 1630:
            start_idx = i
        if 'status_callback(' in line and '🏁 Plan' in line and i > 1700:
            end_idx = i
            break

    if start_idx == -1 or end_idx == -1:
        print('Could not find loop bounds')
        return

    new_lines = lines[:start_idx]
    
    new_lines.append('            total_posted = 0\n')
    new_lines.append('            loop_count = plan.get("loop_count", 1)\n')
    new_lines.append('            loop_delay_minutes = plan.get("loop_delay_minutes", 0)\n\n')
    new_lines.append('            for current_loop in range(loop_count):\n')
    new_lines.append('                if self.stop_requested:\n')
    new_lines.append('                    break\n')
    new_lines.append('                if loop_count > 1:\n')
    new_lines.append('                    status_callback(f"🔄 Starting loop {current_loop + 1}/{loop_count}")\n\n')
    
    # Indent the block (lines from start_idx+1 to end_idx-1)
    for i in range(start_idx + 1, end_idx):
        if lines[i].strip() == '':
            new_lines.append('\n')
        else:
            new_lines.append('    ' + lines[i])
            
    new_lines.append('                if current_loop < loop_count - 1 and not self.stop_requested:\n')
    new_lines.append('                    if loop_delay_minutes > 0:\n')
    new_lines.append('                        status_callback(f"⏳ Loop {current_loop + 1} finished. Waiting {loop_delay_minutes} minute(s) before next loop...")\n')
    new_lines.append('                        wait_end = time.time() + loop_delay_minutes * 60\n')
    new_lines.append('                        while time.time() < wait_end and not self.stop_requested:\n')
    new_lines.append('                            time.sleep(min(10, wait_end - time.time()))\n\n')

    new_lines.extend(lines[end_idx:])

    with open('bot_engine.py', 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print('Successfully updated bot_engine.py')

modify_bot_engine()
