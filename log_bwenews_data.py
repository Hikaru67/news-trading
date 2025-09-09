#!/usr/bin/env python3
"""
BWEnews Data Logger - Monitor and log all BWEnews data to markdown file
"""

import subprocess
import time
import json
from datetime import datetime
import re

def log_bwenews_data():
    """Monitor BWEnews client and log all data to markdown file"""
    
    # Create markdown file with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"BWENEWS_DATA_LOG_{timestamp}.md"
    
    print(f"📊 Starting BWEnews data logging to {log_file}")
    print("🔄 Monitoring BWEnews client for 60 seconds...")
    
    # Initialize markdown content
    markdown_content = f"""# BWEnews Data Log

**Generated**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}  
**Duration**: 60 seconds monitoring  
**Status**: 🟢 **ACTIVE MONITORING**

---

## 📊 **Data Collection Summary**

| Metric | Value |
|--------|-------|
| **Start Time** | {datetime.now().strftime("%H:%M:%S")} |
| **Monitoring Duration** | 60 seconds |
| **Data Source** | BWEnews RSS + WebSocket |
| **Log File** | {log_file} |

---

## 🔄 **Real-time Data Stream**

"""
    
    # Monitor BWEnews client for 60 seconds
    start_time = time.time()
    signal_count = 0
    duplicate_count = 0
    processed_signals = []
    
    try:
        # Start monitoring process
        process = subprocess.Popen(
            ['docker', 'compose', 'logs', '-f', 'bwenews-client'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        while time.time() - start_time < 60:  # Monitor for 60 seconds
            line = process.stdout.readline()
            if line:
                current_time = datetime.now().strftime("%H:%M:%S")
                
                # Parse different types of log entries
                if "Processed BWEnews RSS entry:" in line:
                    signal_count += 1
                    event_type = line.split("Processed BWEnews RSS entry: ")[1].strip()
                    processed_signals.append({
                        'time': current_time,
                        'type': 'PROCESSED',
                        'event_type': event_type,
                        'raw_line': line.strip()
                    })
                    
                    markdown_content += f"### ✅ **Signal #{signal_count} - {event_type}**\n"
                    markdown_content += f"**Time**: {current_time}\n"
                    markdown_content += f"**Status**: Processed\n"
                    markdown_content += f"**Raw Log**: `{line.strip()}`\n\n"
                    
                elif "Published BWEnews signal to Kafka:" in line:
                    signal_id = line.split("Published BWEnews signal to Kafka: ")[1].strip()
                    processed_signals.append({
                        'time': current_time,
                        'type': 'PUBLISHED',
                        'signal_id': signal_id,
                        'raw_line': line.strip()
                    })
                    
                    markdown_content += f"**Kafka Publish**: Signal ID `{signal_id}`\n"
                    markdown_content += f"**Status**: ✅ Published to Kafka\n\n"
                    
                elif "Duplicate RSS entry detected:" in line:
                    duplicate_count += 1
                    duplicate_id = line.split("Duplicate RSS entry detected: ")[1].strip()
                    processed_signals.append({
                        'time': current_time,
                        'type': 'DUPLICATE',
                        'duplicate_id': duplicate_id,
                        'raw_line': line.strip()
                    })
                    
                    markdown_content += f"### 🔄 **Duplicate #{duplicate_count}**\n"
                    markdown_content += f"**Time**: {current_time}\n"
                    markdown_content += f"**Status**: Duplicate detected\n"
                    markdown_content += f"**Signal ID**: `{duplicate_id}`\n"
                    markdown_content += f"**Raw Log**: `{line.strip()}`\n\n"
                    
                elif "Connected to BWEnews WebSocket" in line:
                    processed_signals.append({
                        'time': current_time,
                        'type': 'WEBSOCKET_CONNECT',
                        'raw_line': line.strip()
                    })
                    
                    markdown_content += f"### 🌐 **WebSocket Connection**\n"
                    markdown_content += f"**Time**: {current_time}\n"
                    markdown_content += f"**Status**: ✅ Connected\n"
                    markdown_content += f"**Raw Log**: `{line.strip()}`\n\n"
                    
                elif "Starting BWEnews client" in line:
                    processed_signals.append({
                        'time': current_time,
                        'type': 'STARTUP',
                        'raw_line': line.strip()
                    })
                    
                    markdown_content += f"### 🚀 **Service Startup**\n"
                    markdown_content += f"**Time**: {current_time}\n"
                    markdown_content += f"**Status**: Starting\n"
                    markdown_content += f"**Raw Log**: `{line.strip()}`\n\n"
                
                print(f"[{current_time}] {line.strip()}")
                
    except KeyboardInterrupt:
        print("\n⏹️ Monitoring stopped by user")
    except Exception as e:
        print(f"❌ Error during monitoring: {e}")
    finally:
        if 'process' in locals():
            process.terminate()
    
    # Add summary section
    end_time = datetime.now().strftime("%H:%M:%S")
    markdown_content += f"""
---

## 📈 **Data Collection Summary**

### **Statistics**
| Metric | Count |
|--------|-------|
| **Total Signals Processed** | {signal_count} |
| **Duplicates Detected** | {duplicate_count} |
| **Kafka Messages Published** | {signal_count} |
| **WebSocket Connections** | {len([s for s in processed_signals if s['type'] == 'WEBSOCKET_CONNECT'])} |
| **Monitoring Duration** | 60 seconds |
| **Start Time** | {datetime.now().strftime("%H:%M:%S")} |
| **End Time** | {end_time} |

### **Event Types Processed**
"""
    
    # Count event types
    event_types = {}
    for signal in processed_signals:
        if signal['type'] == 'PROCESSED':
            event_type = signal.get('event_type', 'UNKNOWN')
            event_types[event_type] = event_types.get(event_type, 0) + 1
    
    for event_type, count in event_types.items():
        markdown_content += f"- **{event_type}**: {count} signals\n"
    
    markdown_content += f"""
### **Raw Log Data**
```
"""
    
    # Add all raw log data
    for signal in processed_signals:
        markdown_content += f"[{signal['time']}] {signal['raw_line']}\n"
    
    markdown_content += "```\n\n"
    markdown_content += "---\n"
    markdown_content += f"**Log Generated**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n"
    
    # Write to file
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    
    print(f"\n✅ Data logging completed!")
    print(f"📄 Log file created: {log_file}")
    print(f"📊 Processed {signal_count} signals, {duplicate_count} duplicates")
    
    return log_file

if __name__ == "__main__":
    log_file = log_bwenews_data()
    print(f"\n📖 View the log file: cat {log_file}")
