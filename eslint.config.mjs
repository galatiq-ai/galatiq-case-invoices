import js from "@eslint/js";
import globals from "globals";

// The frontend and CLI reach the backend only through the shared bridge
// (apiFetch() / the CLI client), which attaches x-trace-id and x-client. This
// rule keeps that the only path: a bare fetch() in frontend code is an error.
// api.js is the one sanctioned home for fetch.
export default [
  js.configs.recommended,
  {
    files: ["frontend/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.browser,
    },
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "CallExpression[callee.name='fetch']",
          message:
            "Reach the API through apiFetch() in frontend/api.js — it attaches x-trace-id and x-client. To bypass on purpose, disable this rule on the line with a reason.",
        },
      ],
    },
  },
  {
    files: ["frontend/api.js"],
    rules: { "no-restricted-syntax": "off" },
  },
];
