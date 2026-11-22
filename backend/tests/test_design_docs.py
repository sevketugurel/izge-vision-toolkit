"""Check documented design/API facts against local sources without a browser or DB."""

from pathlib import Path
import re

import pytest

from izge.api import app


ROOT = Path(__file__).resolve().parents[2]
# The Turkish README holds the design-language and API-contract sections these tests check.
README = (ROOT / 'README.tr.md').read_text()
STYLE = (ROOT / 'frontend/src/style.css').read_text()


def section(text, title):
    return text.split(f'## {title}\n', 1)[1].split('\n## ', 1)[0]


def test_documented_palette_matches_stylesheet():
    design = section(README, 'Tasarım dili')
    rows = re.findall(r'^\| [^|]+ \| `([^`]+)` \| `([^`]+)` \| `(#[0-9a-f]+)` \|$', design, re.M)
    assert rows, 'The design table must document CSS selectors, properties and colors'
    for selector, prop, value in rows:
        blocks = re.findall(r'(?:^|\n)' + re.escape(selector) + r'\s*\{([^}]+)\}', STYLE)
        assert any(re.search(r'\b' + re.escape(prop) + r':\s*' + re.escape(value) + r'\s*;', block)
                   for block in blocks), (selector, prop, value)


def test_documented_breakpoints_match_stylesheet():
    design = section(README, 'Tasarım dili')
    documented = set(re.findall(r'`(\d+)px`', design))
    implemented = set(re.findall(r'@media\s*\(max-width:\s*(\d+)px\)', STYLE))
    assert documented == implemented


def test_documented_routes_and_created_responses_match_openapi():
    contract = section(README, 'API sözleşmesi')
    rows = re.findall(r'^\| (GET|POST|PATCH|DELETE) \| `([^`]+)` \| (.+) \|$', contract, re.M)
    assert rows, 'The API table must contain method/path pairs'
    paths = app.openapi()['paths']
    for method, path, description in rows:
        assert path in paths, path
        assert method.lower() in paths[path], (method, path)
        if re.search(r'\b201\b', description):
            assert '201' in paths[path][method.lower()]['responses'], (method, path)


def test_documented_upload_contract_matches_openapi():
    schema = app.openapi()
    upload = schema['paths']['/api/v1/farms/{farm_id}/media']['post']
    assert any(p['name'] == 'camera_id' and p['in'] == 'query' and p['required']
               for p in upload['parameters'])
    body_ref = upload['requestBody']['content']['multipart/form-data']['schema']['$ref']
    body = schema['components']['schemas'][body_ref.rsplit('/', 1)[1]]
    assert 'file' in body['required']
    assert 'recorded_at' in body['properties']
    assert 'recorded_at' not in body['required']
    contract = section(README, 'API sözleşmesi')
    assert 'multipart `file`, isteğe bağlı `recorded_at`' in contract
    assert 'Sorguda `camera_id`' in contract


def test_readme_links_resolve_and_no_screenshots_are_embedded():
    assert not re.search(r'!\[[^\]]*\]\(|<img\b|<picture\b', README, re.I)
    links = re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)', README)
    for target in links:
        if '://' not in target and not target.startswith('#'):
            path = ROOT / target.split('#', 1)[0]
            assert path.is_file(), target
            assert path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg'}, target


@pytest.mark.parametrize('filename', ['README.md', 'README.tr.md', 'SECURITY.md'])
def test_documentation_has_no_merge_conflict_markers(filename):
    text = (ROOT / filename).read_text()
    assert not re.search(r'^(?:<{7}|={7}|>{7})(?:\s.*)?$', text, re.M), filename


def test_sandbox_limits_are_explicit():
    text = README
    assert re.search(r'Playwright e2e.*?SKIP', text, re.S)
    assert 'sandbox dışında' in text
    assert 'görsel inceleme' in text
    assert 'başarılı sayılmaz' in text
