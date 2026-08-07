import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Check, Copy, ChevronRight, Brain } from "lucide-react";
import "./FormattedMessage.css";

interface FormattedMessageProps {
  content: string;
  role: "user" | "ai" | "assistant" | "system";
}

const CodeBlock: React.FC<{ language: string; value: string }> = ({ language, value }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="code-block-wrapper">
      <div className="code-block-header">
        <span className="code-block-lang">{language || "code"}</span>
        <button className="code-block-copy-btn" onClick={handleCopy} title="Copy code">
          {copied ? <Check size={14} className="text-green" /> : <Copy size={14} />}
          <span>{copied ? "Copied!" : "Copy"}</span>
        </button>
      </div>
      <SyntaxHighlighter
        language={language || "text"}
        style={vscDarkPlus as any}
        customStyle={{
          margin: 0,
          borderRadius: "0 0 8px 8px",
          fontSize: "0.875rem",
          background: "#12131a",
        }}
      >
        {value}
      </SyntaxHighlighter>
    </div>
  );
};

export const FormattedMessage: React.FC<FormattedMessageProps> = React.memo(({ content, role }) => {
  const [isThinkingExpanded, setIsThinkingExpanded] = useState(false);

  if (role === "user") {
    return <div className="formatted-user-text">{content}</div>;
  }

  // Extract <think> or <thought> blocks (Only for AI responses)
  let thinkingContent = "";
  let mainContent = content;

  const thinkRegex = /<(?:think|thought)>([\s\S]*?)(?:<\/(?:think|thought)>|$)/i;
  const match = content.match(thinkRegex);

  if (match) {
    thinkingContent = match[1].trim();
    mainContent = content.replace(thinkRegex, "").trim();
  }

  return (
    <div className={`formatted-message-container formatted-${role}-message`}>
      {thinkingContent && (
        <div className="thinking-accordion">
          <button
            className="thinking-accordion-header"
            onClick={() => setIsThinkingExpanded(!isThinkingExpanded)}
          >
            <div className="thinking-accordion-title">
              <Brain size={15} className="thinking-brain-icon" />
              <span>Thought Process</span>
            </div>
            <ChevronRight
              size={15}
              className={`thinking-chevron ${isThinkingExpanded ? "expanded" : ""}`}
            />
          </button>
          {isThinkingExpanded && (
            <div className="thinking-accordion-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{thinkingContent}</ReactMarkdown>
            </div>
          )}
        </div>
      )}

      {mainContent ? (
        <div className="markdown-body">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeRaw]}
            components={{
              code({ node, inline, className, children, ...props }: any) {
                const match = /language-(\w+)/.exec(className || "");
                const codeString = String(children).replace(/\n$/, "");

                if (!inline && (match || codeString.includes("\n"))) {
                  return (
                    <CodeBlock
                      language={match ? match[1] : ""}
                      value={codeString}
                    />
                  );
                }

                return (
                  <code className="inline-code" {...props}>
                    {children}
                  </code>
                );
              },
              a({ href, children }) {
                return (
                  <a href={href} target="_blank" rel="noopener noreferrer" className="markdown-link">
                    {children}
                  </a>
                );
              },
            }}
          >
            {mainContent
              .replace(/<(?:outfit|font:outfit)>([\s\S]*?)<\/(?:outfit|font:outfit)>/gi, '<span class="font-outfit">$1</span>')
              .replace(/\[outfit:([\s\S]*?)\]/gi, '<span class="font-outfit">$1</span>')
              .replace(/<(?:jakarta|font:jakarta)>([\s\S]*?)<\/(?:jakarta|font:jakarta)>/gi, '<span class="font-jakarta">$1</span>')
              .replace(/\[jakarta:([\s\S]*?)\]/gi, '<span class="font-jakarta">$1</span>')
              .replace(/<(?:jetbrains|font:jetbrains)>([\s\S]*?)<\/(?:jetbrains|font:jetbrains)>/gi, '<span class="font-jetbrains">$1</span>')
              .replace(/\[jetbrains:([\s\S]*?)\]/gi, '<span class="font-jetbrains">$1</span>')
              .replace(/<(?:inter|font:inter)>([\s\S]*?)<\/(?:inter|font:inter)>/gi, '<span class="font-inter">$1</span>')
              .replace(/\[inter:([\s\S]*?)\]/gi, '<span class="font-inter">$1</span>')
            }
          </ReactMarkdown>
        </div>
      ) : (
        !thinkingContent && <div className="text-muted">Empty response</div>
      )}
    </div>
  );
});

export default FormattedMessage;
