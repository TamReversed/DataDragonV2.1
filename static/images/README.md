# Static Images Directory

## Logo File Required

To complete the logo integration, place your DataDragon logo image file in this directory.

### File Requirements:
- **File name:** `datadragon-logo.png` (or `.svg`, `.jpg`)
- **Recommended size:** 200x200px or larger (for retina displays)
- **Format:** PNG with transparency (preferred), SVG, or JPG
- **Description:** Dark grey dragon in a circle on black background (or with transparency)

### Logo Specifications:
- The logo should be optimized for web use
- If using PNG, ensure transparency is preserved for the background
- SVG format is recommended for best quality at all sizes
- The logo will be displayed at 80px on the landing page and 40px on other pages

### Current Status:
- Logo CSS styles have been added to all templates
- Logo image tags have been added to all pages
- Logo will automatically hide if the image file is not found (graceful fallback)

### Files Using the Logo:
- `templates/landing.html` - Main landing page (80px logo)
- `templates/index.html` - Excel File Splitter
- `templates/column_analyzer.html` - Column Analyzer
- `templates/column_normalizer.html` - Column Normalizer
- `templates/data_validation.html` - Data Validation
- `templates/data_scrubber.html` - Data Scrubber
- `templates/data_comparison.html` - Data Comparison
- `templates/data_merge.html` - Data Merge/Join
- `templates/duplicate_finder.html` - Duplicate Finder
- `templates/pivot_generator.html` - Pivot Table Generator
- `templates/pdf_to_word.html` - PDF to Word Converter
- `templates/security_info.html` - Security & Privacy Information

Once you place the logo file here, it will automatically appear on all pages!
