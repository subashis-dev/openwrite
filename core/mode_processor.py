import re
import json
import urllib.request
import urllib.error
from typing import Dict, Optional

# Common verbal filler words to remove in offline fallback mode
FILLER_WORDS = [
    r"\b(um+)\b",
    r"\b(uh+)\b",
    r"\b(er+)\b",
    r"\b(ah+)\b",
    r"\b(you know)\b",
    r"\b(so yeah)\b",
]

PUNCTUATION_COMMANDS = [
    (r"\b(full stop|period)\b", "."),
    (r"\b(comma)\b", ","),
    (r"\b(question mark)\b", "?"),
    (r"\b(exclamation mark|exclamation point)\b", "!"),
    (r"\b(colon)\b", ":"),
    (r"\b(semicolon)\b", ";"),
    (r"\b(new line|newline)\b", "\n"),
    (r"\b(new paragraph|next paragraph)\b", "\n\n"),
    (r"\b(open quote|quote)\b", '"'),
    (r"\b(close quote|end quote)\b", '"'),
    (r"\b(open paren|open parenthesis)\b", "("),
    (r"\b(close paren|close parenthesis)\b", ")"),
    (r"\b(open bracket)\b", "["),
    (r"\b(close bracket)\b", "]"),
    (r"\b(open brace|open curly brace)\b", "{"),
    (r"\b(close brace|close curly brace)\b", "}"),
    (r"\b(dash|hyphen)\b", "-"),
    (r"\b(slash|forward slash)\b", "/"),
    (r"\b(backslash)\b", r"\\"),
    (r"\b(at sign)\b", "@"),
    (r"\b(hashtag|hash)\b", "#"),
    (r"\b(percent|percent sign)\b", "%"),
]

CODE_KEYWORD_REPLACEMENTS = [
    (r"\b(fat arrow|arrow function)\b", "=>"),
    (r"\b(arrow)\b", "->"),
    (r"\b(triple equal|strictly equal)\b", "==="),
    (r"\b(double equal|equal equal)\b", "=="),
    (r"\b(not equal)\b", "!="),
    (r"\b(greater than or equal to|greater equal)\b", ">="),
    (r"\b(less than or equal to|less equal)\b", "<="),
    (r"\b(greater than)\b", ">"),
    (r"\b(less than)\b", "<"),
    (r"\b(logical and|and and)\b", "&&"),
    (r"\b(logical or|or or)\b", "||"),
    (r"\b(plus plus|increment)\b", "++"),
    (r"\b(minus minus|decrement)\b", "--"),
    (r"\b(plus equal)\b", "+="),
    (r"\b(minus equal)\b", "-="),
    (r"\b(times equal|multiply equal)\b", "*="),
    (r"\b(divide equal)\b", "/="),
    (r"\b(equal to|equals|equal)\b", "="),
    (r"\b(backtick)\b", "`"),
]


class ModeProcessor:
    def __init__(self, llm_url: str = "http://localhost:1234/v1/chat/completions", ollama_url: Optional[str] = None):
        # Default to LM Studio local server if running, else offline deterministic rules
        self.llm_url = llm_url
        self.ollama_url = ollama_url

    def process(self, raw_text: str, mode: str, selected_text: str = "", app_name: str = "") -> str:
        text = raw_text.strip()
        if not text:
            return ""

        if mode == "dictation":
            return self._process_dictation(text)
        elif mode == "enhancement":
            return self._process_enhancement(text)
        elif mode == "email":
            return self._process_email(text)
        elif mode == "code":
            return self._process_code(text)
        elif mode == "rewrite":
            return self._process_rewrite(text, selected_text)
        elif mode == "assistant":
            return self._process_assistant(text, selected_text)
        else:
            return self._process_enhancement(text)

    def _query_local_llm(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """Queries LM Studio (OpenAI-compatible port 1234) if running, with short timeout."""
        if not self.llm_url:
            return None
        try:
            payload = json.dumps({
                "model": "local-model",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 512
            }).encode("utf-8")
            req = urllib.request.Request(
                self.llm_url,
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"].strip()
                # Strip thinking tags if a reasoning model (like Qwen3.5) outputs <think>...</think>
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                return content if content else None
        except Exception:
            return None

    def _process_dictation(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _process_enhancement(self, text: str) -> str:
        # Try LM Studio first for true AI cleanup/paraphrasing
        llm_out = self._query_local_llm(
            system_prompt="You are a precise dictation formatter. Remove filler words (um, uh), fix punctuation and capitalization, and output ONLY the cleaned text. Never add commentary or change the user's meaning.",
            user_prompt=text
        )
        if llm_out:
            return llm_out

        # Deterministic offline fallback
        processed = text
        for pattern, repl in PUNCTUATION_COMMANDS:
            processed = re.sub(pattern, repl, processed, flags=re.IGNORECASE)
        for filler in FILLER_WORDS:
            processed = re.sub(filler, "", processed, flags=re.IGNORECASE)
        processed = re.sub(r"\s+([,.:;?!])", r"\1", processed)
        processed = re.sub(r"([,.:;?!])(?=[^\s\d\"])", r"\1 ", processed)
        return re.sub(r"[ \t]+", " ", processed).strip()

    def _process_code(self, text: str) -> str:
        processed = text

        def to_camel(match):
            words = match.group(1).split()
            return words[0].lower() + "".join(w.capitalize() for w in words[1:]) if words else ""

        def to_snake(match):
            return "_".join(w.lower() for w in match.group(1).split())

        def to_kebab(match):
            return "-".join(w.lower() for w in match.group(1).split())

        def to_pascal(match):
            return "".join(w.capitalize() for w in match.group(1).split())

        def to_screaming_snake(match):
            return "_".join(w.upper() for w in match.group(1).split())

        var_pattern = r"([a-zA-Z0-9]+(?:\s+[a-zA-Z0-9]+){0,4}?)(?=\s*(?:equal|equals|open|close|fat|\=\>|\=|\;|\:|\.|\,|\n|$))"
        processed = re.sub(r"\bcamel case\s+" + var_pattern, to_camel, processed, flags=re.IGNORECASE)
        processed = re.sub(r"\bsnake case\s+" + var_pattern, to_snake, processed, flags=re.IGNORECASE)
        processed = re.sub(r"\bkebab case\s+" + var_pattern, to_kebab, processed, flags=re.IGNORECASE)
        processed = re.sub(r"\bpascal case\s+" + var_pattern, to_pascal, processed, flags=re.IGNORECASE)
        processed = re.sub(r"\ball caps\s+" + var_pattern, to_screaming_snake, processed, flags=re.IGNORECASE)

        for pattern, repl in CODE_KEYWORD_REPLACEMENTS:
            processed = re.sub(pattern, repl, processed, flags=re.IGNORECASE)
        for pattern, repl in PUNCTUATION_COMMANDS:
            processed = re.sub(pattern, repl, processed, flags=re.IGNORECASE)

        return processed.strip()

    def _process_email(self, text: str) -> str:
        llm_out = self._query_local_llm(
            system_prompt="Format the user's dictated notes into a clean, professional email with a greeting, clear paragraphs or bullets, and a sign-off. Output ONLY the email text.",
            user_prompt=text
        )
        if llm_out:
            return llm_out

        clean = self._process_enhancement(text)
        has_greeting = bool(re.match(r"^(hi|hello|dear|hey|good morning|good afternoon)\b", clean, flags=re.IGNORECASE))
        greeting = "" if has_greeting else "Hi,\n\n"
        has_signoff = bool(re.search(r"\b(best regards|regards|thanks|sincerely|cheers)\b", clean, flags=re.IGNORECASE))
        signoff = "" if has_signoff else "\n\nBest regards,"
        return f"{greeting}{clean}{signoff}"

    def _process_rewrite(self, instruction: str, selected_text: str) -> str:
        if not selected_text:
            llm_out = self._query_local_llm(
                system_prompt="Paraphrase and improve the user's dictated text clearly and concisely. Output ONLY the rewritten text.",
                user_prompt=instruction
            )
            return llm_out if llm_out else self._process_enhancement(instruction)

        llm_out = self._query_local_llm(
            system_prompt="Rewrite the provided text strictly according to the user's instruction. Output ONLY the replacement text, with no extra explanation.",
            user_prompt=f"Text:\n{selected_text}\n\nInstruction: {instruction}"
        )
        if llm_out:
            return llm_out

        inst_lower = instruction.lower()
        if "uppercase" in inst_lower or "all caps" in inst_lower:
            return selected_text.upper()
        if "lowercase" in inst_lower:
            return selected_text.lower()
        if "snake case" in inst_lower:
            return "_".join(w.lower() for w in re.findall(r"\w+", selected_text))
        return selected_text

    def _process_assistant(self, instruction: str, context: str = "") -> str:
        llm_out = self._query_local_llm(
            system_prompt="You are OpenWrite, a concise and helpful voice assistant. Answer the user's question directly and clearly.",
            user_prompt=f"{context}\n{instruction}".strip()
        )
        if llm_out:
            return llm_out
        return f"[LM Studio Offline] Start LM Studio Local Server (port 1234) for live LLM answers. Heard: {instruction}"
