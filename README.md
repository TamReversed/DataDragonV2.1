# Excel File Splitter Web App

A beautiful, easy-to-use web application for splitting large Excel files into smaller, manageable chunks.

## Features

- **Drag & Drop Interface** - Simply drag and drop your Excel file or click to browse
- **Fast Processing** - Efficiently handles files with millions of records
- **Custom Filenames** - Set your own base filename for output files (e.g., "PR_Amounts_Load_1of25.xlsx")
- **Customizable Chunk Size** - Set the number of records per output file
- **Automatic Zip Download** - All split files are automatically packaged into a single ZIP file
- **Modern UI** - Beautiful, responsive design with smooth animations

## Installation

1. Make sure you have Python 3.7+ installed

2. Install the required packages:
```bash
pip install -r requirements.txt
```

## Usage

### Running the Web App

1. Start the Flask server:
```bash
python app.py
```

2. Open your web browser and go to:
```
http://localhost:5000
```

3. Upload your Excel file, set your desired chunk size (default: 40,000 records), and click "Split File"

4. Download the ZIP file containing all your split files!

### Running the Command Line Script

If you prefer to use the command line version:

```bash
python "Data Split.py"
```

Edit the `input_file_path` and `chunk_size` variables in the script as needed.

## How It Works

1. **Upload**: You upload an Excel file (.xlsx or .xls)
2. **Process**: The app reads the file and splits it into chunks based on your specified size
3. **Package**: All split files are packaged into a single ZIP file
4. **Download**: The ZIP file is ready for download with one click

## Configuration

- **Chunk Size**: Default is 40,000 records per file. Adjust based on your needs.
- **Max File Size**: Default is 500MB. Can be adjusted in `app.py` if needed.

## File Structure

```
ERP Data Load Splits/
├── app.py                  # Flask web application
├── Data Split.py           # Command line script
├── templates/
│   └── index.html         # Web interface
├── requirements.txt       # Python dependencies
├── uploads/               # Temporary upload folder (auto-created)
└── output/                # Temporary output folder (auto-created)
```

## Notes

- Uploaded files and output files are automatically cleaned up after processing
- The app runs on port 5000 by default
- All split files maintain the original Excel formatting and data types

## Troubleshooting

**Can't find the Excel file?**
- Make sure the file path is correct
- Use the web app for automatic file handling

**File too large?**
- Increase `MAX_CONTENT_LENGTH` in `app.py`
- Consider splitting locally with the command line script

**Port already in use?**
- Change the port number in `app.py`: `app.run(debug=True, port=XXXX)`

## Support

For issues or questions, contact your system administrator.

