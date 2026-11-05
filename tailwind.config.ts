import type { Config } from "tailwindcss";
const config: Config = { content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"], theme: { extend: { colors: { ink: "#0B0F17", panel: "#111722", purple: "#7C5CFF" }, fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui"] } } }, plugins: [require("@tailwindcss/forms")] };
export default config;
