
import json

def get_split_prompt(sentence, num_parts=2, word_limit=20, language="auto"):
    split_prompt = f"""
## Role
You are a professional Netflix subtitle splitter in **{language}**.

## Task
Split the given subtitle text into **{num_parts}** parts, each less than **{word_limit}** words.

1. Maintain sentence meaning coherence according to Netflix subtitle standards
2. MOST IMPORTANT: Keep parts roughly equal in length (minimum 3 words each)
3. Split at natural points like punctuation marks or conjunctions
4. If provided text is repeated words, simply split at the middle of the repeated words.

## Steps
1. Analyze the sentence structure, complexity, and key splitting challenges
2. Generate two alternative splitting approaches with [br] tags at split positions
3. Compare both approaches highlighting their strengths and weaknesses
4. Choose the best splitting approach

## Given Text
<split_this_sentence>
{sentence}
</split_this_sentence>

## Output in only JSON format and no other text
```json
{{
    "analysis": "Brief description of sentence structure, complexity, and key splitting challenges",
    "split1": "First splitting approach with [br] tags at split positions",
    "split2": "Alternative splitting approach with [br] tags at split positions",
    "assess": "Comparison of both approaches highlighting their strengths and weaknesses",
    "choice": "1 or 2"
}}
```

Note: Start you answer with ```json and end with ```, do not add any other text.
""".strip()
    return split_prompt

def get_summary_prompt(source_content, src_lang, tgt_lang, custom_terms_json=None):
    # add custom terms note
    terms_note = ""
    if custom_terms_json:
        terms_list = []
        for term in custom_terms_json.get('terms', []):
            terms_list.append(f"- {term['src']}: {term['tgt']} ({term['note']})")
        terms_note = "\n### Existing Terms\nPlease exclude these terms in your extraction:\n" + "\n".join(terms_list)
    
    summary_prompt = f"""
## Role
You are a video translation expert and terminology consultant, specializing in {src_lang} comprehension and {tgt_lang} expression optimization.

## Task
For the provided {src_lang} video text:
1. Summarize main topic in two sentences
2. Extract professional terms/names with {tgt_lang} translations (excluding existing terms)
3. Provide brief explanation for each term

{terms_note}

Steps:
1. Topic Summary:
   - Quick scan for general understanding
   - Write two sentences: first for main topic, second for key point
2. Term Extraction:
   - Mark professional terms and names (excluding those listed in Existing Terms)
   - Provide {tgt_lang} translation or keep original
   - Add brief explanation
   - Extract less than 15 terms

## INPUT
<text>
{source_content}
</text>

## Output in only JSON format and no other text
{{
  "theme": "Two-sentence video summary",
  "terms": [
    {{
      "src": "{src_lang} term",
      "tgt": "{tgt_lang} translation or original", 
      "note": "Brief explanation"
    }},
    ...
  ]
}}  

## Example
{{
  "theme": "本视频介绍人工智能在医疗领域的应用现状。重点展示了AI在医学影像诊断和药物研发中的突破性进展。",
  "terms": [
    {{
      "src": "Machine Learning",
      "tgt": "机器学习",
      "note": "AI的核心技术，通过数据训练实现智能决策"
    }},
    {{
      "src": "CNN",
      "tgt": "CNN",
      "note": "卷积神经网络，用于医学图像识别的深度学习模型"
    }}
  ]
}}

Note: Start you answer with ```json and end with ```, do not add any other text.
""".strip()
    return summary_prompt

def generate_shared_prompt(previous_content_prompt, after_content_prompt, summary_prompt, things_to_note_prompt):
    context = ""
    if previous_content_prompt:
        context += f"""### Context Information
<previous_content>
{previous_content_prompt}
</previous_content>
"""
    if after_content_prompt:
        context += f"""
<subsequent_content>
{after_content_prompt}
</subsequent_content>
"""
    if summary_prompt:
        context += f"""
### Content Summary
{summary_prompt}
"""
    if things_to_note_prompt:
        context += f"""
### Points to Note
{things_to_note_prompt}
"""
    return context

def get_prompt_faithfulness(lines, shared_prompt, src_lang, tgt_lang):
    # Split lines by \n
    line_splits = lines.split('\n')
    
    json_dict = {}
    for i, line in enumerate(line_splits):
        json_dict[f"{i}"] = {"origin": line, "direct": f"direct {tgt_lang} translation {i}."}
    json_format = json.dumps(json_dict, indent=2, ensure_ascii=False)

    prompt_faithfulness = f'''
## Role
You are a professional Netflix subtitle translator, fluent in both {src_lang} and {tgt_lang}, as well as their respective cultures. 
Your expertise lies in accurately understanding the semantics and structure of the original {src_lang} text and faithfully translating it into {tgt_lang} while preserving the original meaning.

## Task
We have a segment of original {src_lang} subtitles that need to be directly translated into {tgt_lang}. These subtitles come from a specific context and may contain specific themes and terminology.

1. Translate the original {src_lang} subtitles into {tgt_lang} line by line
2. Ensure the translation is faithful to the original, accurately conveying the original meaning
3. Consider the context and professional terminology

{shared_prompt}

<translation_principles>
1. Faithful to the original: Accurately convey the content and meaning of the original text, without arbitrarily changing, adding, or omitting content.
2. Accurate terminology: Use professional terms correctly and maintain consistency in terminology.
3. Understand the context: Fully comprehend and reflect the background and contextual relationships of the text.
</translation_principles>

## INPUT
<subtitles>
{lines}
</subtitles>

## Output in only JSON format and no other text
```json
{json_format}
```

Note: Start you answer with ```json and end with ```, do not add any other text.
'''
    return prompt_faithfulness.strip()


def get_prompt_expressiveness(faithfulness_result, lines, shared_prompt, src_lang, tgt_lang):
    json_format = {
        key: {
            "origin": value.get("origin", ""),
            "direct": value.get("direct", ""),
            "reflect": "your reflection on direct translation",
            "free": "your free translation"
        }
        for key, value in faithfulness_result.items()
        if isinstance(value, dict)
    }
    json_format_str = json.dumps(json_format, indent=2, ensure_ascii=False)

    prompt_expressiveness = f'''
## Role
You are a professional Netflix subtitle translator and language consultant.
Your expertise lies not only in accurately understanding the original {src_lang} but also in optimizing the {tgt_lang} translation to better suit the target language's expression habits and cultural background.

## Task
We already have a direct translation version of the original {src_lang} subtitles.
Your task is to reflect on and improve these direct translations to create more natural and fluent {tgt_lang} subtitles.

1. Analyze the direct translation results line by line, pointing out existing issues
2. Provide detailed modification suggestions
3. Perform free translation based on your analysis
4. Do not add comments or explanations in the translation, as the subtitles are for the audience to read
5. Do not leave empty lines in the free translation, as the subtitles are for the audience to read

{shared_prompt}

<Translation Analysis Steps>
Please use a two-step thinking process to handle the text line by line:

1. Direct Translation Reflection:
   - Evaluate language fluency
   - Check if the language style is consistent with the original text
   - Check the conciseness of the subtitles, point out where the translation is too wordy

2. {tgt_lang} Free Translation:
   - Aim for contextual smoothness and naturalness, conforming to {tgt_lang} expression habits
   - Ensure it's easy for {tgt_lang} audience to understand and accept
   - Adapt the language style to match the theme (e.g., use casual language for tutorials, professional terminology for technical content, formal language for documentaries)
</Translation Analysis Steps>
   
## INPUT
<subtitles>
{lines}
</subtitles>

## Output in only JSON format and no other text
```json
{json_format_str}
```

Note: Start you answer with ```json and end with ```, do not add any other text.
'''
    return prompt_expressiveness.strip()

def get_prompt_correction(lines, src_lang):
    line_splits = lines.split('\n')
    json_dict = {}
    for i, line in enumerate(line_splits):
        json_dict[f"{i}"] = {"original": line, "corrected": line}
    json_format = json.dumps(json_dict, indent=2, ensure_ascii=False)

    prompt = f'''
## Role
You are a professional subtitle proofreader and editor in {src_lang}.

## Task
Correct the following subtitle lines for typos, homophones, or obvious transcription errors.

## IMPORTANT CONSTRAINTS
1. **LENGTH MUST MATCH**: The corrected text MUST have roughly the same number of words/characters as the original.
2. **NO TIMING SHIFT**: Do NOT split or merge lines. Keep exactly one output line for one input line.
3. **MINIMAL CHANGE**: Only fix errors. If a line is correct, return it exactly as is.
4. **NO PARAPHRASING**: Do not rephrase or rewrite for style. Only fix OBJECTIVE errors.

## INPUT
{lines}

## Output in specific JSON format
```json
{json_format}
```

Note: Start you answer with ```json and end with ```, do not add any other text.
'''.strip()
    return prompt
