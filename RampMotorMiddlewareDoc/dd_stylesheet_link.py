from pathlib import Path
import re
import posixpath

ROOT = Path(".").resolve()
STYLE_FILE = ROOT / "styles" / "styles.css"

# File HTML không muốn sửa
EXCLUDE_FILES = {
    "index.html",
}

# Có tạo backup .bak hay không
CREATE_BACKUP = True


def to_html_href(from_file: Path, target_file: Path) -> str:
    """
    Create a POSIX-style relative href from an HTML file to target CSS file.
    Example:
      doc_alg/Algorithm_1shunt.html -> ../styles/styles.css
      some/deep/file.html            -> ../../styles/styles.css
    """
    rel_path = target_file.relative_to(ROOT)
    from_dir = from_file.parent.relative_to(ROOT)

    # posixpath.relpath uses "/" even on Windows if input strings use "/"
    href = posixpath.relpath(
        rel_path.as_posix(),
        start=from_dir.as_posix() if from_dir.as_posix() != "." else "."
    )
    return href


def remove_existing_styles_link(html: str) -> str:
    """
    Remove existing link tags that point to styles/styles.css or styles.css.
    This avoids duplicate links and also moves the link to the correct position.
    """
    pattern = re.compile(
        r'\s*<link\s+[^>]*rel=["\']stylesheet["\'][^>]*href=["\'][^"\']*styles/styles\.css["\'][^>]*>\s*'
        r'|\s*<link\s+[^>]*href=["\'][^"\']*styles/styles\.css["\'][^>]*rel=["\']stylesheet["\'][^>]*>\s*'
        r'|\s*<link\s+[^>]*rel=["\']stylesheet["\'][^>]*href=["\'][^"\']*styles\.css["\'][^>]*>\s*'
        r'|\s*<link\s+[^>]*href=["\'][^"\']*styles\.css["\'][^>]*rel=["\']stylesheet["\'][^>]*>\s*',
        re.IGNORECASE
    )
    return pattern.sub("\n", html)


def add_stylesheet_link(html_file: Path) -> bool:
    if html_file.name in EXCLUDE_FILES:
        return False

    html = html_file.read_text(encoding="utf-8")

    if "</head>" not in html.lower():
        print(f"Skip: {html_file} does not contain </head>")
        return False

    href = to_html_href(html_file, STYLE_FILE)
    link_tag = f'  <link rel="stylesheet" href="{href}">'

    cleaned_html = remove_existing_styles_link(html)

    # Insert right before </head>, so this CSS is loaded after Pandoc inline <style>
    new_html = re.sub(
        r'</head>',
        link_tag + "\n</head>",
        cleaned_html,
        count=1,
        flags=re.IGNORECASE
    )

    if new_html == html:
        return False

    if CREATE_BACKUP:
        backup_file = html_file.with_suffix(html_file.suffix + ".bak")
        if not backup_file.exists():
            backup_file.write_text(html, encoding="utf-8")

    html_file.write_text(new_html, encoding="utf-8")
    print(f"Updated: {html_file.relative_to(ROOT)} -> {href}")
    return True


def main():
    if not STYLE_FILE.exists():
        raise FileNotFoundError(f"Cannot find stylesheet: {STYLE_FILE}")

    updated_count = 0

    for html_file in ROOT.rglob("*.html"):
        # Skip files inside styles folder if any
        if "styles" in html_file.relative_to(ROOT).parts:
            continue

        if add_stylesheet_link(html_file):
            updated_count += 1

    print()
    print(f"Done. Updated {updated_count} HTML file(s).")


if __name__ == "__main__":
    main()