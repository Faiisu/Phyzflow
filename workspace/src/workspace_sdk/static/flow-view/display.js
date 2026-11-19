export function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function appendSyntaxTokens(parent, tokens) {
  for (const token of tokens) {
    if (typeof token === "string") {
      parent.append(token);
    } else {
      const span = element("span", "", "token");
      span.classList.add(token.type, ...[token.alias ?? []].flat());
      appendSyntaxTokens(span, [token.content].flat());
      parent.append(span);
    }
  }
}

export function highlightCode(code, text, language) {
  const prism = window.Prism;
  const grammar = prism?.languages[language];
  if (!grammar) {
    code.textContent = text;
    return false;
  }
  const preview = document.createDocumentFragment();
  appendSyntaxTokens(preview, prism.tokenize(text, grammar));
  code.replaceChildren(preview);
  return true;
}

export function jsonOutput(text) {
  const code = element("code", "");
  highlightCode(code, text, "json");
  const pre = element("pre", "", "json-output");
  pre.append(code);
  return pre;
}

export const labels = { running: "Running", succeeded: "Succeeded", failed: "Failed", skipped: "Skipped", stopped: "Stopped" };

export function formatDuration(value) {
  if (!Number.isFinite(value)) return "";
  if (value > 0 && value < 0.001) return "<0.001 ms";
  return `${value.toFixed(value < 1 ? 3 : 2)} ms`;
}

export function blockedByText(blockers, statusFor) {
  const names = blockers.map(id => {
    const status = labels[statusFor(id)];
    return status ? `${id} (${status})` : id;
  });
  return `Blocked by ${names.join(", ")}`;
}

export function inputBindings(step, source) {
  return step.arguments
    .filter(argument => argument.kind === "node" && argument.source === source)
    .map(argument => argument.name);
}

export function locationText(location) {
  if (!location.file) return "Source unavailable";
  return location.line == null ? location.file : `${location.file}:${location.line}`;
}
