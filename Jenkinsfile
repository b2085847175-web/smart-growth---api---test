/*
 * /chat/answer 定时全量回归流水线。
 *
 * 每天 02:30 在 Windows 节点上跑 data/answer 下全部 dev 环境用例（2916 条，含 19 个
 * 纯逻辑单测共 2935 个用例），用 pytest-xdist 并行，把串行 8 小时压到 1-2 小时。
 *
 * 几个刻意为之的地方：
 *
 * - venv 建在 workspace 之外（VENV_DIR），构建之间复用；按 requirements.txt 的
 *   SHA256 判断要不要重装依赖，避免每次构建都花几分钟装包。
 *
 * - 凭据全部走 Jenkins credentials。注入的环境变量优先于仓库里的 .env，
 *   这是 config/project_env.py 里 reload_project_env 的既定行为。
 *
 * - 先跑纯逻辑单测再跑真实用例。单测挂了说明是环境或代码问题，没必要再花两小时
 *   去打接口，也能避免把基础设施故障误读成"AI 回复不稳定"。
 *
 * - 两个测试文件（test_answer_yaml.py / test_daily_usage.py）都读 ANSWER_ENTRY，
 *   必须分开调用，否则同一份数据会被加载两遍。
 *
 * - ENV 显式设成 dev。仓库 .env 里当前是 ENV=console，不覆盖的话，任何没声明
 *   target_env 的 YAML 都会落到生产环境去。
 */
pipeline {
    agent { label 'windows' }

    options {
        disableConcurrentBuilds()
        timestamps()
        timeout(time: 300, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
    }

    triggers {
        // Jenkins 服务器时区，每天 02:30。
        cron('30 2 * * *')
    }

    parameters {
        choice(
            name: 'ENTRY',
            choices: ['all', 'daily', 'regression'],
            description: '执行入口。all = data/answer 下全部 dev 用例（2916 条）；daily = 116 条；regression = 1202 条。'
        )
        string(
            name: 'WORKERS',
            defaultValue: '8',
            description: 'pytest-xdist 并发进程数。全量建议 8；daily 用 1-4 就够了。'
        )
        booleanParam(
            name: 'COLLECT_ONLY',
            defaultValue: false,
            description: '只做用例收集校验，不发任何请求。改数据文件后可以先勾这个验证。'
        )
        booleanParam(
            name: 'NOTIFY_ON_SUCCESS',
            defaultValue: true,
            description: '成功时也推企微/钉钉。关掉则只在失败时通知。'
        )
    }

    environment {
        PYTHONUTF8 = '1'

        // 见文件头说明：必须显式覆盖 .env 里的 ENV=console。
        ENV = 'dev'

        ANSWER_ENTRY = "${params.ENTRY}"
        WORKERS      = "${params.WORKERS}"

        // venv 建在 workspace 之外，构建之间复用。
        VENV_DIR   = 'C:\\jenkins-tools\\venv-answer-test'
        REPORT_DIR = 'reports\\jenkins'

        // 节点访问不了公网 PyPI 时改这里，或换成内网镜像。
        PIP_INDEX_URL = 'https://pypi.tuna.tsinghua.edu.cn/simple'

        // 测试店铺配置。仓库 .env 里也有一份，Jenkins 注入的优先级更高。
        CHAT_SHOP_ID_DEV   = '690'
        CHAT_SHOP_NAME_DEV = 'shop_690'
        CHAT_ACCOUNT_DEV   = 'zhaowenlong01'
    }

    stages {
        stage('Workspace') {
            steps {
                powershell '''
                    $ErrorActionPreference = "Stop"
                    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
                    # 清掉上一次的产物，避免构建早退时把旧报告当成新结果发出去。
                    if (Test-Path $env:REPORT_DIR) { Remove-Item -Recurse -Force $env:REPORT_DIR }
                    New-Item -ItemType Directory -Force -Path $env:REPORT_DIR | Out-Null
                    Write-Host "工作区：$(Get-Location)"
                    Write-Host "入口：$env:ANSWER_ENTRY  并发：$env:WORKERS"
                '''
            }
        }

        stage('Prepare python env') {
            steps {
                powershell '''
                    $ErrorActionPreference = "Stop"
                    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
                    $python = Join-Path $env:VENV_DIR "Scripts\\python.exe"

                    if (-not (Test-Path $python)) {
                        Write-Host "创建 venv：$env:VENV_DIR"
                        New-Item -ItemType Directory -Force -Path (Split-Path $env:VENV_DIR) | Out-Null
                        python -m venv $env:VENV_DIR
                        if (-not (Test-Path $python)) { throw "venv 创建失败，请确认节点上 python 在 PATH 里" }
                    }

                    # requirements.txt 没变就不重装。
                    $want  = (Get-FileHash -Algorithm SHA256 requirements.txt).Hash
                    $stamp = Join-Path $env:VENV_DIR "requirements.sha256"
                    $have  = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { "" }

                    if ($have -ne $want) {
                        Write-Host "依赖有变化，安装 requirements.txt"
                        & $python -m pip install --quiet --upgrade pip
                        & $python -m pip install --quiet -r requirements.txt
                        if ($LASTEXITCODE -ne 0) { throw "pip install 失败（exit=$LASTEXITCODE）" }
                        Set-Content -Path $stamp -Value $want -NoNewline
                    } else {
                        Write-Host "依赖未变化，跳过安装"
                    }

                    & $python --version
                    & $python -m pip list --format=freeze | Select-String -Pattern "^pytest"
                '''
            }
        }

        stage('Validate data') {
            steps {
                powershell '''
                    $ErrorActionPreference = "Stop"
                    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
                    $python = Join-Path $env:VENV_DIR "Scripts\\python.exe"

                    $collectArgs = @(
                        "run_tests.py"
                        "--env", "dev"
                        "--pattern", "test_answer_yaml.py"
                        "--collect-only", "-q"
                    )
                    # 只接 stdout。这里不能写 2>&1：$ErrorActionPreference="Stop" 配 2>&1 会把
                    # 原生命令的 stderr 输出当成 NativeCommandError 抛出来（实测 PowerShell 5.1
                    # 会抛 RemoteException）。pytest 的收集统计行本来就在 stdout 上。
                    $output = & $python @collectArgs | Out-String
                    $exit = $LASTEXITCODE
                    Write-Host $output
                    if ($exit -ne 0) { throw "用例收集失败（exit=$exit）" }

                    # 收集数低于该入口的预期下限，说明有数据文件没注册进 config/answer_entries.py，
                    # 或者文件被误删 —— 这种情况下"跑绿"是假绿。
                    $floor = switch ($env:ANSWER_ENTRY) {
                        "all"        { 2900 }
                        "regression" { 1200 }
                        "daily"      { 116 }
                        default      { 1 }
                    }
                    if ($output -notmatch "(\\d+)(?:/(\\d+))? tests? collected") {
                        throw "没能从收集输出里解析出用例数，检查 pytest 版本是否变了"
                    }
                    $collected = if ($Matches[2]) { [int]$Matches[2] } else { [int]$Matches[1] }
                    if ($collected -lt $floor) {
                        throw "入口 $env:ANSWER_ENTRY 只收集到 $collected 条，低于下限 $floor。检查 config/answer_entries.py 是否漏了文件。"
                    }
                    Write-Host "收集校验通过：$collected 条（下限 $floor）"
                '''
            }
        }

        stage('Unit tests') {
            when { expression { return !params.COLLECT_ONLY } }
            steps {
                powershell '''
                    $ErrorActionPreference = "Stop"
                    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
                    $python = Join-Path $env:VENV_DIR "Scripts\\python.exe"

                    # 这一批是纯逻辑单测，不发请求。--deselect 去掉参数化出来的
                    # 网络用例（同模块内，-k 过滤会连单测一起排掉，只能用 --deselect）。
                    $unitArgs = @(
                        "-m", "pytest"
                        "testcases\\answer\\test_answer_yaml.py"
                        "--deselect", "testcases/answer/test_answer_yaml.py::test_answer_yaml"
                        "--junitxml=$env:REPORT_DIR\\junit-unit.xml"
                        "-q"
                    )
                    & $python @unitArgs
                    if ($LASTEXITCODE -ne 0) { throw "单测失败（exit=$LASTEXITCODE），先修环境再跑接口用例" }
                '''
            }
        }

        stage('Run answer cases') {
            when { expression { return !params.COLLECT_ONLY } }
            steps {
                powershell '''
                    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
                    $python = Join-Path $env:VENV_DIR "Scripts\\python.exe"

                    $pytestArgs = @(
                        "run_tests.py"
                        "--env", "dev"
                        "--pattern", "test_answer_yaml.py"
                        "--junitxml=$env:REPORT_DIR\\junit.xml"
                        "-n", $env:WORKERS
                    )
                    & $python @pytestArgs
                    $exit = $LASTEXITCODE
                    Write-Host "pytest 退出码：$exit"
                    if ($exit -ne 0) { exit $exit }
                '''
            }
        }
    }

    post {
        always {
            // 只发布主用例的 junit；单测那份单独归档，避免同一批单测在报告里出现两次。
            junit testResults: 'reports/jenkins/junit.xml', allowEmptyResults: true
            archiveArtifacts artifacts: 'reports/jenkins/**', allowEmptyArchive: true

            script {
                notifyBuild(currentBuild.currentResult)
            }
        }
    }
}

/**
 * 推企微 + 钉钉。用 try/catch 兜住：凭据没建、webhook 失效都不该改变构建结论。
 * 需要的 Jenkins 凭据（String 类型）：
 *   answer-wecom-webhook / answer-dingtalk-webhook / answer-dingtalk-secret
 */
def notifyBuild(String result) {
    // post 阶段 currentBuild.duration 未必已结算，用起始时间自己算。
    def elapsedSeconds = (System.currentTimeMillis() - currentBuild.startTimeInMillis) / 1000

    try {
        withCredentials([
            string(credentialsId: 'answer-wecom-webhook', variable: 'WECOM_WEBHOOK'),
            string(credentialsId: 'answer-dingtalk-webhook', variable: 'DINGTALK_WEBHOOK'),
            string(credentialsId: 'answer-dingtalk-secret', variable: 'DINGTALK_SECRET')
        ]) {
            powershell """
                [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
                \$python = Join-Path '${env.VENV_DIR}' 'Scripts\\python.exe'
                \$notifyArgs = @(
                    'scripts\\notify_ci.py'
                    '--junit', '${env.REPORT_DIR}\\junit.xml'
                    '--entry', '${env.ANSWER_ENTRY}'
                    '--status', '${result}'
                    '--duration-seconds', '${elapsedSeconds}'
                    '--build-number', '${env.BUILD_NUMBER}'
                    '--build-url', '${env.BUILD_URL}'
                )
                # 条件参数必须用追加的方式。写成内联插值的话，不启用时会往数组里塞一个
                # 空字符串元素，splat 出去 argparse 会当成多余参数直接报错。
                \$extra = '${params.NOTIFY_ON_SUCCESS ? '' : '--only-on-failure'}'
                if (\$extra) { \$notifyArgs += \$extra }

                & \$python @notifyArgs
                Write-Host "通知脚本退出码：\$LASTEXITCODE"
            """
        }
    } catch (err) {
        echo "通知发送失败（不影响构建结论）：${err}"
    }
}
