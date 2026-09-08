"""
Prompt templates.

The Jinja templates live next to this module, as `.md.j2` files. `get_prompt_template` prefers a copy in the
user data directory, so an operator can change a prompt without a new release.
"""

from importlib.resources import files

from niitti.paths import data_dir

PROMPT_TEMPLATE_ARTICLE_TITLE = "artcile_title_inst.md.j2"
PROMPT_TEMPLATE_ARTICLE = "article.md.j2"
PROMPT_TEMPLATE_ARTICLE_UPDATED = "article_updated.md.j2"
PROMPT_TEMPLATE_FEEDBACK = "feedback.md.j2"

PROMPT_ENCODING = "utf-8"

_PACKAGE = "meri"
"""Templates and the user override directory both belong to `meri`, not to this submodule."""


def get_prompt_template(template_name: str) -> str:
    """
    Get the prompt text based on the template name.

    Searches for the prompt template in the user data directory first, then in the package data directory.
    """

    # Hack-ish approach; append md.j2 if necessary"
    prompt_file_name = template_name
    if not template_name.endswith(".md.j2"):
        prompt_file_name += ".md.j2"
    user_prompt_dir = data_dir(_PACKAGE) / "prompts"

    user_prompt_file = user_prompt_dir / prompt_file_name
    if user_prompt_file.exists():
        return user_prompt_file.read_text(encoding=PROMPT_ENCODING)

    # Check from package data directory
    resource = f"prompts/{prompt_file_name}"
    return files(_PACKAGE).joinpath(resource).read_text(encoding=PROMPT_ENCODING)
