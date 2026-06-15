import "./styles/globals.css";

export const metadata = {
  title: "Booktree",
  description: "Booktree exception queue and metadata repair UI",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
