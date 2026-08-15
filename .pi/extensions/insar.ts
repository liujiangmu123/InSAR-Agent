/**
 * 项目级自动加载入口:pi 在本目录(已信任)启动时自动加载 .pi/extensions/*,
 * 终端 pi、pi Desktop、pi-web 因此都能拿到 insar 扩展,无需 -e 标志。
 * 真身在 pi-insar/src/index.ts,这里只做转发。
 */
export { default } from "../../pi-insar/src/index.ts";
