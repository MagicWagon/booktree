import { jsonError, runWorker } from "@/src/lib/worker";

export async function GET(request) {
  try {
    const { searchParams } = new URL(request.url);
    const path = searchParams.get("path") || "";
    const args = path ? ["get-config", "--path", path] : ["get-config"];
    return Response.json(await runWorker(args));
  } catch (error) {
    return jsonError(error);
  }
}

export async function PATCH(request) {
  try {
    const body = await request.json();
    return Response.json(
      await runWorker([
        "save-config",
        "--path",
        body.path || "",
        "--payload",
        JSON.stringify(body.config || {}),
      ])
    );
  } catch (error) {
    return jsonError(error);
  }
}
