/*
 * Daily scheduled conversation-data job.
 *
 * The Jenkins node is expected to be Windows because this repository uses the
 * checked-in .venv\Scripts\python.exe launcher.  Change the node label if the
 * Jenkins installation uses a different Windows label.
 */
pipeline {
    agent { label 'windows' }

    options {
        // Never allow two daily runs to create duplicate data at the same time.
        disableConcurrentBuilds()
        timestamps()
        timeout(time: 120, unit: 'MINUTES')
    }

    triggers {
        // China Standard Time: every day at 02:30.
        cron('30 2 * * *')
    }

    environment {
        // The YAML pack also declares dev, but keep the Jenkins choice explicit.
        ENV = 'dev'
        ANSWER_ENTRY = 'daily'
        CHAT_SHOP_ID_DEV = '585'
        CHAT_SHOP_NAME_DEV = 'shop_585'
        CHAT_ACCOUNT_DEV = 'zhaowenlong01'
        PYTHONUTF8 = '1'
        SCHEDULED_REPORT_DIR = 'reports\\scheduled'
    }

    stages {
        stage('Prepare') {
            steps {
                powershell '''
                    $ErrorActionPreference = "Stop"
                    if (-not (Test-Path -LiteralPath ".venv\\Scripts\\python.exe")) {
                        throw "Python launcher not found: .venv\\Scripts\\python.exe"
                    }
                    if (-not (Test-Path -LiteralPath "data\\scheduled\\manifest.yaml")) {
                        throw "Scheduled data manifest not found"
                    }
                    New-Item -ItemType Directory -Force -Path $env:SCHEDULED_REPORT_DIR | Out-Null
                '''
            }
        }

        stage('Validate scheduled pack') {
            steps {
                powershell '''
                    $ErrorActionPreference = "Stop"
                    & .\\.venv\\Scripts\\python.exe -m pytest `
                        testcases\\answer\\test_answer_yaml.py `
                        --collect-only -q
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                '''
            }
        }

        stage('Generate conversations') {
            steps {
                // Credentials are configured in Jenkins as username/password
                // id "zhiyan-dev-login"; the password is never written to logs.
                withCredentials([usernamePassword(
                    credentialsId: 'zhiyan-dev-login',
                    usernameVariable: 'LOGIN_ACCOUNT_DEV',
                    passwordVariable: 'LOGIN_PASSWORD_DEV'
                )]) {
                    powershell '''
                        $ErrorActionPreference = "Stop"
                        & .\\.venv\\Scripts\\python.exe -m pytest `
                            testcases\\answer\\test_answer_yaml.py `
                            -q `
                            --junitxml="$env:SCHEDULED_REPORT_DIR\\scheduled-junit.xml"
                        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    '''
                }
            }
        }
    }

    post {
        always {
            junit testResults: 'reports/scheduled/scheduled-junit.xml', allowEmptyResults: true
            archiveArtifacts artifacts: 'reports/scheduled/**', allowEmptyArchive: true
        }
        success {
            echo 'Scheduled conversation-data generation completed.'
        }
        failure {
            echo 'Scheduled conversation-data generation failed. Review the archived report and failed cases.'
        }
    }
}
