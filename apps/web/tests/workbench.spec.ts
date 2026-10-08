import { test, expect } from "@playwright/test";

test.beforeEach(async ({page}) => {
  const username = process.env.E2E_USERNAME;
  const password = process.env.E2E_PASSWORD;
  if (!username || !password) throw new Error("请提供本地测试账号 E2E_USERNAME 和 E2E_PASSWORD");
  const response = await page.request.post("/api/auth/login", {data: {username, password}});
  expect(response.ok()).toBeTruthy();
  await page.goto("/");
  await page.getByRole("button", {name: "深度研究", exact: true}).click();
});

test("项目、可编辑计划、成果与证据完整闭环", async ({page}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await expect(page.getByText("演示模式", {exact: true})).toBeVisible();
  await page.getByRole("button", {name: /新建研究项目/}).click();
  await page.getByPlaceholder("例如：AI 研究产品竞品分析").fill(`浏览器验收 ${Date.now()}`);
  await page.getByRole("button", {name: "创建项目", exact: true}).click();
  await page.getByRole("button", {name: "研究范围"}).click();
  await page.getByLabel("研究对象", {exact: true}).fill("ChatGPT, Gemini");
  await page.getByLabel("比较维度", {exact: true}).fill("产品形态, 交付方式");
  await page.getByLabel("研究目标", {exact: true}).fill("比较 AI 深度研究产品的产品形态、研究流程和交付方式");
  await page.getByRole("button", {name: "提交研究"}).click();
  await expect(page.getByText("先对齐研究方向")).toBeVisible();
  await expect(page.getByText(/默认范围：截至/)).toBeVisible();
  await page.getByLabel("计划研究对象").fill("ChatGPT, Gemini");
  await page.getByLabel("计划比较维度").fill("产品形态, 交付方式");
  // Polling must not erase the user's plan edits.
  await page.waitForTimeout(2300);
  await expect(page.getByLabel("计划研究对象")).toHaveValue("ChatGPT, Gemini");
  await page.getByRole("button", {name: "确认并开始研究"}).click();
  await expect(page.locator(".status.completed")).toBeVisible();
  await expect(page.getByText("模拟：ChatGPT 的产品形态研究已生成示例证据。请配置真实服务获取事实。", {exact: true})).toBeVisible();
  await page.getByRole("button", {name: "原文证据 1", exact: true}).first().click();
  await expect(page.locator(".evidence-drawer")).toBeVisible();
  await expect(page.getByText("模拟来源 · 不是真实产品信息", {exact: true})).toBeVisible();
  await page.getByRole("button", {name: "关闭证据"}).click();
  await page.getByRole("button", {name: "对比表", exact: true}).click();
  await expect(page.locator("table")).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", {name: "导出 CSV"}).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.csv$/);
  await page.getByRole("button", {name: "执行轨迹", exact: true}).click();
  await expect(page.getByText("成果已交付", {exact: true})).toBeVisible();
  expect(errors).toEqual([]);
});

test("上传项目资料并切换项目", async ({page}) => {
  await page.getByRole("button", {name: /新建研究项目/}).click();
  await page.getByPlaceholder("例如：AI 研究产品竞品分析").fill(`资料项目 ${Date.now()}`);
  await page.getByRole("button", {name: "创建项目", exact: true}).click();
  await page.locator('input[type="file"]').setInputFiles({name: "context.md", mimeType: "text/markdown", buffer: Buffer.from("# 研究背景\n\n关注团队协作与证据溯源。")});
  await page.getByRole("button", {name: /项目资料/}).click();
  await expect(page.getByRole("heading", {name: "context.md"})).toBeVisible();
});

test("新增普通会话、持续对话与刷新历史", async ({page}) => {
  await page.getByRole("button", {name: "普通会话", exact: true}).click();
  await page.getByRole("button", {name: "新增会话", exact: true}).click();
  const question = `会话验收 ${Date.now()}`;
  await page.getByLabel("会话消息", {exact: true}).fill(question);
  await page.getByRole("button", {name: "发送消息", exact: true}).click();
  await expect(page.locator(".message.assistant.completed")).toHaveCount(1);
  await page.getByLabel("会话消息", {exact: true}).fill("继续讨论上一条问题");
  await page.getByRole("button", {name: "发送消息", exact: true}).click();
  await expect(page.locator(".message.assistant.completed")).toHaveCount(2);
  await expect(page.locator(".message.assistant.completed").last()).toContainText(question);
  await page.reload();
  await expect(page.locator(".message")).toHaveCount(4);
  await page.getByRole("button", {name: "新增会话", exact: true}).click();
  await expect(page.locator(".message")).toHaveCount(0);
});
