import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import App from './App'

describe('App', () => {
  it('starts a courtroom with the selected role', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('radio', { name: '辩护方' }))
    await user.click(screen.getByRole('button', { name: /开始庭审/ }))

    expect(screen.getByText('公开庭审记录')).toBeInTheDocument()
    expect(screen.getByText('辩护方材料')).toBeInTheDocument()
  })
})
