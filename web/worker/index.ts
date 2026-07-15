import handler from "vinext/server/app-router-entry";

export default {
  fetch(request: Request, env: unknown, ctx: ExecutionContext): Promise<Response> {
    return handler.fetch(request, env, ctx);
  },
};
