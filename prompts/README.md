# Prompts Directory

This directory contains all prompt templates used across the BRD generation and chat Lambda functions.

## 📁 Structure

```
prompts/
├── __init__.py                    # Package initialization, exports main functions
├── brd_generator_prompts.py       # BRD generation prompt templates
└── README.md                      # This file
```

## 🎯 Purpose

Separating prompts from code logic provides several benefits:

1. **Maintainability** - Update prompts without touching business logic
2. **Version Control** - Track prompt changes independently
3. **Collaboration** - Non-technical team members can review/edit prompts
4. **Testing** - Easier to A/B test different prompt variations
5. **Reusability** - Share prompts across different Lambda functions
6. **Cleaner Code** - Separation of concerns

## 📝 Usage

### In Lambda Functions

```python
from prompts.brd_generator_prompts import (
    get_full_brd_generation_prompt,
    PromptConfig
)

# Generate the full prompt
prompt = get_full_brd_generation_prompt(template_text, transcript_text)

# Use configuration constants
max_tokens = PromptConfig.MAX_INPUT_TOKENS
estimated_tokens = PromptConfig.estimate_tokens(text)
```

### Available Functions

#### `get_full_brd_generation_prompt(template_text, transcript_text)`
Constructs the complete BRD generation prompt with the provided template and transcript.

**Parameters:**
- `template_text` (str): The BRD template structure to follow
- `transcript_text` (str): The meeting transcript to extract information from

**Returns:**
- `str`: Complete prompt ready for LLM invocation

#### `get_prompt_base_length()`
Returns the character length of the base prompt (without template/transcript).

**Returns:**
- `int`: Character count of base prompt components

### Configuration Constants

The `PromptConfig` class provides configuration constants and helper methods:

```python
class PromptConfig:
    CHARS_PER_TOKEN = 4                  # Token estimation ratio
    MAX_INPUT_TOKENS = 2000              # Maximum input tokens
    SAFETY_MARGIN_TOKENS = 200           # Safety margin for calculations
    RESERVED_TEMPLATE_TOKENS = 600       # Reserved tokens for template
    MIN_TRANSCRIPT_TOKENS = 500          # Minimum tokens for transcript
    TOTAL_CONTEXT_TOKENS = 8192          # Total context window (Llama 3.1 8B)
    
    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        """Estimate token count from character count"""
        
    @classmethod
    def calculate_available_output_tokens(cls, prompt_tokens: int) -> int:
        """Calculate available tokens for output"""
```

## 🔧 Modifying Prompts

### To Update BRD Generation Prompts:

1. Edit `brd_generator_prompts.py`
2. Modify the relevant constant:
   - `BRD_GENERATION_PROMPT_BASE` - Main task description
   - `BRD_REQUIRED_SECTIONS` - List of required sections
   - `BRD_GENERATION_INSTRUCTIONS` - Detailed instructions

3. Test the changes locally before deploying

### To Add New Prompt Templates:

1. Create a new file (e.g., `chat_prompts.py`)
2. Define your prompt constants and functions
3. Export them in `__init__.py`
4. Import and use in your Lambda function

## 📋 Prompt Template Structure

### BRD Generation Prompt Components:

1. **Base Prompt** (`BRD_GENERATION_PROMPT_BASE`)
   - Introduces the task and role
   - Sets expectations for output format
   - Emphasizes conciseness

2. **Required Sections** (`BRD_REQUIRED_SECTIONS`)
   - Lists all 16 mandatory BRD sections
   - Ensures comprehensive coverage

3. **Instructions** (`BRD_GENERATION_INSTRUCTIONS`)
   - Detailed guidance on structure preservation
   - Rules for handling missing information
   - Formatting guidelines

## 🚀 Best Practices

1. **Keep prompts concise** - Shorter prompts leave more room for output
2. **Use clear instructions** - Be explicit about requirements
3. **Version control** - Document significant prompt changes in git commits
4. **Test thoroughly** - Always test prompt changes before production deployment
5. **Document changes** - Add comments explaining why prompts were modified

## 🔄 Migration Guide

If you need to migrate existing hardcoded prompts:

1. Extract the prompt text from the Lambda function
2. Create appropriate constants in the prompts module
3. Create a function to construct the full prompt
4. Update the Lambda function to import and use the new function
5. Test to ensure behavior is unchanged

## 📚 Examples

### Example 1: Basic Usage

```python
from prompts.brd_generator_prompts import get_full_brd_generation_prompt

template = "Your BRD template here..."
transcript = "Meeting transcript here..."

prompt = get_full_brd_generation_prompt(template, transcript)
# Use prompt with your LLM
```

### Example 2: Token Estimation

```python
from prompts.brd_generator_prompts import PromptConfig

text = "Some long text..."
estimated_tokens = PromptConfig.estimate_tokens(text)
print(f"Estimated tokens: {estimated_tokens}")
```

### Example 3: Dynamic Token Calculation

```python
from prompts.brd_generator_prompts import PromptConfig

prompt_tokens = 1500
available_output = PromptConfig.calculate_available_output_tokens(prompt_tokens)
print(f"Available tokens for output: {available_output}")
```

## 🐛 Troubleshooting

### Issue: Import errors
**Solution:** Ensure the `prompts` directory is in your Python path or Lambda deployment package.

### Issue: Token limit exceeded
**Solution:** Adjust `PromptConfig` constants or truncate input text before prompt generation.

### Issue: Prompt not following expected format
**Solution:** Review the prompt constants and ensure all required components are included.

## 📞 Support

For questions or issues related to prompt templates, please:
1. Check this README
2. Review the source code in `brd_generator_prompts.py`
3. Contact the development team

---

**Last Updated:** 2026-01-20
**Maintained By:** AgentCore Development Team
