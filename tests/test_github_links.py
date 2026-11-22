from src.sources.github import extract_repo_urls


def test_extracts_and_canonicalises():
    text = "Code is available at https://github.com/foo/bar. See also github.com/Baz/qux.git and (https://www.github.com/a/b-c)."
    assert extract_repo_urls(text) == [
        "https://github.com/foo/bar",
        "https://github.com/Baz/qux",
        "https://github.com/a/b-c",
    ]


def test_ignores_site_pages_and_lookalikes():
    text = "https://github.com/features/actions, mygithub.com/x/y, https://foo.github.io/proj, github.com/onlyowner"
    assert extract_repo_urls(text) == []


def test_dedup_case_insensitive_and_subpaths():
    assert extract_repo_urls("github.com/Foo/Bar/tree/main", "https://github.com/foo/bar") == ["https://github.com/Foo/Bar"]


def test_repo_names_with_dots_kept():
    assert extract_repo_urls("see github.com/huggingface/transformers.js") == ["https://github.com/huggingface/transformers.js"]
