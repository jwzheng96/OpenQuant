import { RouterProvider } from "@tanstack/react-router";
import { router } from "@/lib/router";

export default function App() {
  return <RouterProvider router={router} />;
}
