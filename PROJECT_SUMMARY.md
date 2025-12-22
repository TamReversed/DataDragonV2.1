# Excel File Splitter - Project Summary

**Created:** November 3, 2025  
**Status:** Production Ready  
**Version:** 2.0 (Future Mode Edition)

---

## Project Overview

A professional web application for splitting large Excel files into smaller, manageable chunks with real-time progress tracking and dual-theme UI.

### Original Problem
- Large Excel files (1M+ rows) need to be split for ERP data loads
- Manual process was time-consuming and error-prone
- Need for consistent file naming conventions

### Solution Delivered
Full-featured web application with:
- Drag & drop file upload
- Custom chunk size configuration
- Custom filename patterns (e.g., `PR_Amounts_Load_1of25.xlsx`)
- Real-time progress tracking with Server-Sent Events
- Automatic ZIP packaging
- Beautiful dual-theme UI (Classic & Future modes)
- Customizable animated backgrounds

---

## Project Structure

```
ERP Data Load Splits/
├── app.py                      # Flask web server (262 lines)
├── Data Split.py               # Command-line script (45 lines)
├── templates/
│   └── index.html             # Web interface (1056 lines)
├── requirements.txt           # Python dependencies
├── README.md                  # User documentation
├── PROJECT_SUMMARY.md         # This file
├── .gitignore                 # Git ignore rules
├── uploads/                   # Temporary upload folder (auto-created)
└── output/                    # Temporary output folder (auto-created)
```

---

## ✨ Key Features

### Core Functionality
1. **File Upload**
   - Drag & drop interface
   - File validation (.xlsx, .xls only)
   - 500MB max file size
   - Secure filename handling

2. **File Splitting**
   - Customizable chunk size (default: 40,000 records)
   - Custom base filename support
   - Sequential numbering: `[Name]_1of25.xlsx`, `[Name]_2of25.xlsx`, etc.
   - Maintains Excel formatting and data types

3. **Real-Time Progress**
   - Server-Sent Events (SSE) for live updates
   - Progress bar with percentage
   - Status messages ("Creating file X of Y...")
   - Keep-alive pings to prevent timeouts

4. **Output Packaging**
   - Automatic ZIP compression
   - One-click download
   - Automatic cleanup of temporary files

### UI/UX Features

#### Classic Mode (Default)
- Beautiful purple gradient background
- Clean white cards
- Professional design
- Fully responsive

#### Future Mode (Beta) 🚀
- **Dark Theme**
  - Deep black background (#0a0a0a)
  - Glassmorphism (frosted glass effect)
  - Semi-transparent panels with backdrop blur
  - Purple/violet accent colors

- **Animated Background**
  - 3 floating color blobs
  - Lava lamp effect with screen blending
  - Smooth, perpetual animations
  - Customizable colors and speeds

- **Customization Panel**
  - 3 color pickers for blob colors
  - Animation speed slider (10s - 60s)
  - Real-time preview
  - Settings persist via localStorage
  - Reset to defaults button

---

## Technical Implementation

### Backend (Flask + Python)

**Key Technologies:**
- Flask 3.1.2
- Pandas 2.1.4
- OpenPyXL 3.1.2
- Server-Sent Events (SSE)
- Threading for background processing

**Architecture:**
```python
# Main Components:
1. Flask Routes
   - / (index)
   - /upload (POST) - Handles file upload
   - /progress/<session_id> (SSE) - Real-time updates
   - /download/<filename> - File download

2. Background Processing
   - Async file splitting in separate thread
   - Progress queue for SSE communication
   - Automatic cleanup after completion

3. File Processing
   - split_excel_file() - Main splitting logic
   - progress_queue - SSE communication
   - buildBaselineMap() - Helper function
```

**Session Management:**
- Unique session IDs (timestamp-based)
- Progress queues dictionary
- Automatic cleanup after completion

### Frontend (HTML + CSS + JavaScript)

**Key Technologies:**
- Vanilla JavaScript (no frameworks)
- CSS3 animations and transitions
- Glassmorphism effects
- Server-Sent Events API
- localStorage for preferences

**UI Components:**
```javascript
// Main Elements:
1. Theme Toggle
   - Classic ↔ Future mode switcher
   - Persistent preference

2. Customization Panel (Future Mode)
   - Color pickers (3)
   - Speed slider
   - Reset button

3. File Upload Area
   - Drag & drop zone
   - Click to browse
   - File info display

4. Progress Tracking
   - Real-time progress bar
   - Status messages
   - SSE connection management

5. Results Display
   - Success message
   - Download button
   - Statistics (rows, files)
```

**CSS Features:**
- Glassmorphism with `backdrop-filter: blur(40px)`
- CSS animations for blob movement
- Mix-blend-mode for lava lamp effect
- Smooth transitions (0.3s - 0.5s)
- Responsive design

---

## 🎨 Theme System

### Classic Mode
```css
Background: linear-gradient(135deg, #667eea 0%, #764ba2 100%)
Container: white
Text: #333
Accents: Purple gradients
```

### Future Mode
```css
Background: #0a0a0a + animated blobs
Container: rgba(30, 30, 30, 0.7) + backdrop-filter
Text: #fff with subtle glows
Accents: Purple/violet with transparency
Borders: rgba(255, 255, 255, 0.1)
```

### Animated Background
```javascript
// 3 Blobs with properties:
Blob 1: 400px, Purple (#667eea), 25s animation
Blob 2: 350px, Violet (#764ba2), 30s animation
Blob 3: 300px, Blue (#38bdf8), 35s animation

// Animation:
- Float in different directions
- Scale between 0.9x and 1.1x
- Screen blend mode for overlapping colors
- Blur: 60px for soft edges
```

---

## Data Persistence

### localStorage Keys:
```javascript
'uiTheme'          // 'classic' or 'future'
'blobColors'       // JSON: {color1, color2, color3}
'animationSpeed'   // Number: 10-60
```

### Session Data:
- Temporary files cleaned after processing
- Progress queues removed after completion
- ZIP files available for download

---

## 🚀 Deployment

### Local Development
```bash
# Start server
cd "/path/to/project"
python3 app.py

# Access at:
http://127.0.0.1:5000
```

### Requirements
```
Python 3.7+
Flask==3.1.2
pandas==2.1.4
openpyxl==3.1.2
Werkzeug==3.1.3
```

---

## Performance

### Tested With:
- 40,000 rows → 1 file (instant)
- 1,000,000 rows → 25 files (40K chunks)
- Files with 10 records → 4,000 files

### Optimization:
- Background threading prevents UI blocking
- SSE keeps connection alive
- Automatic cleanup prevents disk bloat
- ZIP compression reduces download size

---

## Use Cases

### Primary Use Case: ERP Data Loads
```
Problem: Need to load 1M records into ERP system
Limit: 40,000 records per file
Solution: Upload file → Set chunk to 40,000 → Split → Download 25 files
Result: Files named PR_Amounts_Load_1of25.xlsx through PR_Amounts_Load_25of25.xlsx
```

### Secondary Use Cases:
- Email attachment size limits
- Database import batching
- Performance testing with smaller datasets
- Data distribution across teams

---

## Customization Guide

### Adding New Color Schemes:
```javascript
// In templates/index.html
const presets = {
    ocean: ['#006994', '#0099cc', '#4dd2ff'],
    sunset: ['#ff6b6b', '#ee5a6f', '#c44569'],
    forest: ['#27ae60', '#16a085', '#1abc9c']
};
```

### Changing Default Settings:
```javascript
// In templates/index.html
const defaultSettings = {
    color1: '#667eea',  // Change blob 1 color
    color2: '#764ba2',  // Change blob 2 color
    color3: '#38bdf8',  // Change blob 3 color
    speed: 30           // Change animation speed (seconds)
};
```

### Adjusting Chunk Size:
```python
# In app.py
app.config['DEFAULT_CHUNK_SIZE'] = 40000  # Records per file
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB
```

---

## 🐛 Known Issues & Limitations

### Current Limitations:
1. **File Size:** 500MB max (configurable)
2. **Format Support:** Excel only (.xlsx, .xls)
3. **Browser Support:** Modern browsers only (SSE requirement)
4. **Concurrent Users:** Single-threaded Flask (use production WSGI for scale)

### Future Enhancements:
- [ ] CSV format support
- [ ] Multiple file upload
- [ ] Preset color themes
- [ ] File preview before splitting
- [ ] Download individual files (not just ZIP)
- [ ] Progress persistence across page refreshes
- [ ] User accounts and history

---

## 📝 Version History

### Version 2.0 - Future Mode Edition (Nov 3, 2025)
- ✨ Added Future (beta) dark theme
- 🎨 Animated lava lamp background
- Customization panel (colors & speed)
- localStorage persistence
- Real-time customization

### Version 1.5 - Progress Update (Nov 3, 2025)
- Real-time progress tracking with SSE
- Keep-alive mechanism
- 💬 Live status messages
- Background processing

### Version 1.0 - Initial Release (Nov 3, 2025)
- File upload and splitting
- Custom filename support
- ZIP packaging
- 🎨 Classic theme UI

---

## 🙏 Acknowledgments

Built with assistance from Claude (Anthropic) using:
- Modern web development best practices
- Apple-inspired design principles
- Real-time communication patterns
- User experience optimization

---

## 📄 License

Internal project for Proven Optics / US Navy ERP Data Management

---

## 🔗 Quick Links

- **Server:** `http://127.0.0.1:5000`
- **GitHub:** (Add if needed)
- **Documentation:** See README.md

---

## 💡 Tips & Tricks

### For Best Performance:
1. Use Chrome or Edge for best SSE support
2. Keep chunk size reasonable (10K - 100K records)
3. Close settings panel during processing
4. Clear old ZIP files periodically

### For Best Experience:
1. Try Future mode in a dark room
2. Slow down animation speed (60s) for zen mode
3. Experiment with color combinations
4. Use custom filenames for better organization

### Command Line Alternative:
```python
# Edit Data Split.py and run:
python3 "Data Split.py"

# Configure:
input_file_path = 'your_file.xlsx'
base_filename = "Custom_Name"
chunk_size = 40000
```

---

**End of Summary** 🎉

*This document captures the complete state of the Excel File Splitter project as of November 3, 2025.*

