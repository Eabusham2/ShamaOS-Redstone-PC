from shamaos.gpu import Framebuffer, GPUCommand, GPUOp, GPUReference


def test_line_and_rect():
    fb = Framebuffer(32, 16)
    fb.line(0, 0, 5, 0)
    assert all(fb.get(x, 0) for x in range(6))
    fb.rect(2, 2, 4, 3, fill=False)
    assert fb.get(2, 2)
    assert fb.get(5, 4)


def test_double_buffer_swap():
    gpu = GPUReference(16, 8)
    gpu.execute(GPUCommand(GPUOp.SET_PIXEL, (3, 4)))
    assert gpu.front.get(3, 4) == 0
    gpu.execute(GPUCommand(GPUOp.SWAP_BUFFER))
    assert gpu.front.get(3, 4) == 1
