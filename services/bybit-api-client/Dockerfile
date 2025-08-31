# Build stage
FROM golang:1.21-alpine AS builder

WORKDIR /app

# Install git and build dependencies
RUN apk add --no-cache git

# Copy go mod files
COPY services/bybit-api-client/go.mod ./

# Download dependencies and create go.sum
RUN go mod download && go mod tidy && go mod verify

# Copy source code
COPY services/bybit-api-client/ .

# Get dependencies
RUN go get .

# Build the binary
RUN CGO_ENABLED=0 GOOS=linux go build -a -installsuffix cgo -o main .

# Final stage
FROM alpine:latest

# Install ca-certificates for HTTPS requests
RUN apk --no-cache add ca-certificates

WORKDIR /root/

# Copy the binary from builder stage
COPY --from=builder /app/main .

# Run the binary
CMD ["./main"]
