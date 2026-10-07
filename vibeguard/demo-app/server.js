const express = require("express");
const app = express();
app.use(express.urlencoded({ extended: false }));

const USERS = { "demo@vibeguard.dev": "password123" };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sessionUser = (req) => {
  const m = /(?:^|; )session=([^;]+)/.exec(req.headers.cookie || "");
  return m ? decodeURIComponent(m[1]) : null;
};
const layout = (title, body) => `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title></head>
<body><nav><a href="/login">Login</a> <a href="/register">Register</a> <a href="/dashboard">Dashboard</a></nav>
<main>${body}</main></body></html>`;

app.get("/", (req, res) => res.redirect("/login"));

app.get("/login", (req, res) => {
  const err = req.query.error ? '<p role="alert">Invalid email or password</p>' : "";
  res.send(layout("Login", `<h1>Sign in</h1>${err}
<form method="post" action="/login">
<label for="email">Email</label><input id="email" name="email" type="email">
<label for="password">Password</label><input id="password" name="password" type="password">
<button type="submit">Sign in</button></form>`));
});

app.post("/login", (req, res) => {
  const { email, password } = req.body;
  if (USERS[email] && USERS[email] === password) {
    res.setHeader("Set-Cookie", `session=${encodeURIComponent(email)}; HttpOnly; Path=/`);
    return res.redirect("/dashboard");
  }
  res.redirect("/login?error=1");
});

app.get("/register", (req, res) =>
  res.send(layout("Register", `<h1>Create account</h1>
<form method="post" action="/register" novalidate>
<label for="name">Name</label><input id="name" name="name" type="text">
<label for="remail">Email</label><input id="remail" name="email" type="email">
<button type="submit">Register</button></form>`)));

app.post("/register", (req, res) => res.redirect("/login"));

app.get("/dashboard", (req, res) => {
  if (!sessionUser(req)) return res.redirect("/login");
  const user = sessionUser(req) || "guest";
  res.send(layout("Dashboard", `<h1>Dashboard</h1><p>Welcome, ${esc(user)}</p>
<p><a href="/logout">Logout</a> <button id="refresh" type="button">Refresh</button> <button id="settings" type="button">Settings</button></p>`));
});

app.get("/logout", (req, res) => {
  res.setHeader("Set-Cookie", "session=; Max-Age=0; Path=/");
  res.redirect("/login");
});

const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`demo-app on http://localhost:${port}`));
