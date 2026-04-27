import React from 'react';
import { Result, Button, Typography } from 'antd';

const { Paragraph, Text } = Typography;

interface Props {
  children: React.ReactNode;
  /** Pass current pathname to auto-reset on navigation */
  resetKey?: string;
}

interface State {
  hasError: boolean;
  error?: Error;
  errorInfo?: React.ErrorInfo;
}

class ErrorBoundary extends React.Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    this.setState({ errorInfo });
    console.error('[ErrorBoundary]', error, errorInfo);
  }

  componentDidUpdate(prevProps: Props) {
    // Auto-reset when route changes (resetKey changes)
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, error: undefined, errorInfo: undefined });
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 40 }}>
          <Result
            status="error"
            title="页面渲染异常"
            subTitle="请尝试刷新页面或联系管理员"
            extra={[
              <Button type="primary" key="reload" onClick={() => this.setState({ hasError: false })}>
                重试当前页
              </Button>,
              <Button key="back" onClick={() => { this.setState({ hasError: false }); window.history.back(); }}>
                返回上一页
              </Button>,
            ]}
          >
            <div style={{ background: 'var(--lm-bg-elevated)', borderRadius: 8, padding: 16, marginTop: 16 }}>
              <Paragraph>
                <Text strong style={{ color: 'var(--lm-critical)' }}>
                  {this.state.error?.message}
                </Text>
              </Paragraph>
              <Paragraph style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
                <pre style={{ maxHeight: 200, overflow: 'auto', fontSize: 11, whiteSpace: 'pre-wrap' }}>
                  {this.state.errorInfo?.componentStack}
                </pre>
              </Paragraph>
            </div>
          </Result>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
